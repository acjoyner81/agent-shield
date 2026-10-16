package com.agentshield.gateway.telemetry;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.connection.RedisStreamCommands.XAddOptions;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

/**
 * Edge telemetry emitter. Writes the 0008 CloudEvents envelope to the same
 * telemetry:queue stream the Python aggregator reads, with source
 * agentshield-gateway-java. Only events that happen at the edge belong here:
 * auth rejections and routing failures. A request Python served is counted once,
 * by Python.
 *
 * Emission is best effort and fire and forget. It never blocks and never changes
 * the caller's auth or routing outcome; a Redis outage loses edge events, which
 * the spec accepts on purpose.
 */
@Component
public class EdgeTelemetryEmitter {

    public static final String SOURCE = "agentshield-gateway-java";
    public static final String QUEUE_KEY = "telemetry:queue";

    /** Matches the Python producer's QUEUE_MAXLEN so both writers agree on the cap. */
    public static final long QUEUE_MAXLEN = 100_000L;

    public static final String TYPE_AUTHZ_FAILURE = "agentshield.security.authz_failure";
    public static final String TYPE_REQUEST_COMPLETED = "agentshield.telemetry.request.completed";

    private static final Logger log = LoggerFactory.getLogger(EdgeTelemetryEmitter.class);

    private final ReactiveStringRedisTemplate redis;
    private final ObjectMapper mapper;

    public EdgeTelemetryEmitter(ReactiveStringRedisTemplate redis, ObjectMapper mapper) {
        this.redis = redis;
        this.mapper = mapper;
    }

    /** G-AC-3: one authz_failure event for a token the edge refused. */
    public String authzFailure(String tenantId) {
        return emit(TYPE_AUTHZ_FAILURE, tenantId, null, null, null, securityDenied());
    }

    /** G-AC-4: one request.completed event for a routing failure at the edge. */
    public String requestCompleted(String tenantId, String method, String path, int statusCode, double latencyMs) {
        return emit(TYPE_REQUEST_COMPLETED, tenantId, null, null, null, httpOutcome(method, path, statusCode, latencyMs));
    }

    public String emit(String type, String tenantId, String userId, String traceId, String spanId,
            Map<String, Object> data) {
        String payload = envelope(type, tenantId, userId, traceId, spanId, data);
        try {
            redis.opsForStream()
                    .add(QUEUE_KEY, Map.of("payload", payload),
                            XAddOptions.maxlen(QUEUE_MAXLEN).approximateTrimming(true))
                    .doOnError(e -> log.warn("edge telemetry emit failed for {}: {}", type, e.getMessage()))
                    .onErrorResume(e -> Mono.empty())
                    .subscribe();
        } catch (RuntimeException e) {
            // Building the client side command can fail before a publisher exists,
            // for example a closed connection factory. Telemetry is never allowed
            // to change the caller's outcome, so log and move on.
            log.warn("edge telemetry emit failed for {}: {}", type, e.getMessage());
        }
        return payload;
    }

    String envelope(String type, String tenantId, String userId, String traceId, String spanId,
            Map<String, Object> data) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("specversion", "1.0");
        event.put("event_id", "evt_" + UUID.randomUUID().toString().replace("-", ""));
        event.put("timestamp", DateTimeFormatter.ISO_INSTANT.format(Instant.now()));
        event.put("source", SOURCE);
        event.put("type", type);
        event.put("tenant_id", tenantId == null || tenantId.isBlank() ? "unknown" : tenantId);
        event.put("user_id", userId);
        event.put("trace_id", traceId);
        event.put("span_id", spanId);
        event.put("data", data == null ? Map.of() : data);
        try {
            return mapper.writeValueAsString(event);
        } catch (JsonProcessingException e) {
            log.warn("edge telemetry serialization failed for {}: {}", type, e.getMessage());
            return "{\"specversion\":\"1.0\",\"source\":\"" + SOURCE + "\",\"type\":\"" + type
                    + "\",\"tenant_id\":\"unknown\",\"data\":{}}";
        }
    }

    private static Map<String, Object> securityDenied() {
        return Map.of(
                "rbac_passed", false,
                "rate_limit_remaining", 0,
                "tripwire_flagged", false);
    }

    private static Map<String, Object> httpOutcome(String method, String path, int statusCode, double latencyMs) {
        Map<String, Object> http = new LinkedHashMap<>();
        http.put("method", method);
        http.put("path", path);
        http.put("status_code", statusCode);
        http.put("latency_ms", latencyMs);
        return http;
    }
}
