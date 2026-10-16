package com.agentshield.gateway.telemetry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.lang.reflect.Field;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.connection.RedisStreamCommands.XAddOptions;
import org.springframework.data.redis.core.ReactiveStreamOperations;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import reactor.core.publisher.Mono;

/**
 * G-AC-3/G-AC-4: an edge event is a single 0008 CloudEvents envelope with
 * source agentshield-gateway-java, written to telemetry:queue with the shared
 * queue cap. These are pure emitter guarantees; the routing and rejection
 * wiring is covered by the filter and security tests.
 */
class EdgeTelemetryEmitterTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final ReactiveStringRedisTemplate redis = org.mockito.Mockito.mock(ReactiveStringRedisTemplate.class);

    @SuppressWarnings("unchecked")
    private final ReactiveStreamOperations<String, Object, Object> stream =
            org.mockito.Mockito.mock(ReactiveStreamOperations.class);

    private final EdgeTelemetryEmitter emitter = new EdgeTelemetryEmitter(redis, MAPPER);

    @BeforeEach
    void stubStream() {
        when(redis.opsForStream()).thenReturn(stream);
        when(stream.add(anyString(), any(), any(XAddOptions.class))).thenReturn(Mono.empty());
    }

    @Test
    void authzFailureWritesOneCloudEventsEnvelope() throws Exception {
        emitter.authzFailure("tenant_alpha");

        JsonNode event = capturedEvent();
        assertThat(event.get("source").asText()).isEqualTo("agentshield-gateway-java");
        assertThat(event.get("specversion").asText()).isEqualTo("1.0");
        assertThat(event.get("type").asText()).isEqualTo(EdgeTelemetryEmitter.TYPE_AUTHZ_FAILURE);
        assertThat(event.get("tenant_id").asText()).isEqualTo("tenant_alpha");
        assertThat(event.get("event_id").asText()).startsWith("evt_");
        assertThat(event.get("timestamp").asText()).isNotBlank();
        assertThat(event.get("data").get("rbac_passed").asBoolean()).isFalse();
    }

    @Test
    void requestCompletedWritesTheHttpOutcome() throws Exception {
        emitter.requestCompleted("tenant_alpha", "GET", "/v1/chat/x", 502, 3.5);

        JsonNode event = capturedEvent();
        assertThat(event.get("type").asText()).isEqualTo(EdgeTelemetryEmitter.TYPE_REQUEST_COMPLETED);
        JsonNode data = event.get("data");
        assertThat(data.get("method").asText()).isEqualTo("GET");
        assertThat(data.get("path").asText()).isEqualTo("/v1/chat/x");
        assertThat(data.get("status_code").asInt()).isEqualTo(502);
        assertThat(data.get("latency_ms").asDouble()).isEqualTo(3.5);
    }

    @Test
    void blankTenantDefaultsToUnknown() throws Exception {
        emitter.authzFailure("   ");
        assertThat(capturedEvent().get("tenant_id").asText()).isEqualTo("unknown");
    }

    @Test
    void nullTenantDefaultsToUnknown() throws Exception {
        emitter.authzFailure(null);
        assertThat(capturedEvent().get("tenant_id").asText()).isEqualTo("unknown");
    }

    @Test
    void theCapMatchesThePythonProducer() {
        assertThat(EdgeTelemetryEmitter.QUEUE_MAXLEN).isEqualTo(100_000L);
    }

    private JsonNode capturedEvent() throws Exception {
        ArgumentCaptor<Map<String, String>> body = ArgumentCaptor.forClass(Map.class);
        ArgumentCaptor<XAddOptions> options = ArgumentCaptor.forClass(XAddOptions.class);
        verify(stream).add(eq(EdgeTelemetryEmitter.QUEUE_KEY), body.capture(), options.capture());

        assertThat(body.getValue()).containsOnlyKeys("payload");
        assertThat(maxlenOf(options.getValue())).isEqualTo(EdgeTelemetryEmitter.QUEUE_MAXLEN);
        return MAPPER.readTree(body.getValue().get("payload"));
    }

    private static Long maxlenOf(XAddOptions options) throws Exception {
        Field field = XAddOptions.class.getDeclaredField("maxlen");
        field.setAccessible(true);
        return (Long) field.get(options);
    }
}
