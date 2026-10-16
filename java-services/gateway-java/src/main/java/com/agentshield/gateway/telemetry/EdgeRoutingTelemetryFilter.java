package com.agentshield.gateway.telemetry;

import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.core.Ordered;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

/**
 * G-AC-4: emits one request.completed event for a routing failure at the edge,
 * and nothing for a request Python actually served (Python owns that event).
 *
 * A routing failure is a request the edge could not hand off: no route matched
 * (404 with no matched route on the exchange), or the downstream produced no
 * response at all (502/504). A 404 returned by a matched route is a served
 * response, not an edge failure, so it is left to the downstream to report.
 *
 * Realized as a WebFlux WebFilter rather than a Spring Cloud Gateway
 * GlobalFilter, because a Gateway GlobalFilter only runs inside the gateway
 * handler and can never observe the framework's own no-route 404.
 *
 * A failure arrives one of two ways, and both are handled here so exactly one
 * event is emitted:
 *   - as a response status, when the downstream answered with 502/504 itself;
 *     that path completes normally and is read after the fold.
 *   - as a thrown exception, when the downstream produced no response at all
 *     (connection failure) or no route matched (framework 404). An exception
 *     skips the completion continuation, so it is classified in onErrorResume
 *     and re-propagated for the error handler to render the response.
 *
 * Order 0 runs after the security filter chain (order -100) so the validated JWT
 * is available, and before the gateway handler, so this filter wraps the
 * dispatch and can read the final response status.
 */
@Component
public class EdgeRoutingTelemetryFilter implements WebFilter, Ordered {

    private final EdgeTelemetryEmitter emitter;
    private final EdgeTenantResolver resolver;

    public EdgeRoutingTelemetryFilter(EdgeTelemetryEmitter emitter, EdgeTenantResolver resolver) {
        this.emitter = emitter;
        this.resolver = resolver;
    }

    @Override
    public int getOrder() {
        return 0;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        long startNanos = System.nanoTime();
        return chain.filter(exchange)
                .onErrorResume(ex -> onFailure(exchange, ex, startNanos))
                .then(Mono.defer(() -> afterCompletion(exchange, startNanos)));
    }

    /**
     * A routing failure thrown rather than returned. Emit first, then
     * re-propagate, so the default error handler still renders the response
     * (a connectivity failure becomes a 502/504, a no-route failure a 404).
     */
    private Mono<Void> onFailure(ServerWebExchange exchange, Throwable ex, long startNanos) {
        int statusCode = thrownFailureStatus(exchange, ex);
        if (statusCode == 0) {
            return Mono.error(ex);
        }
        return emit(exchange, statusCode, startNanos).then(Mono.error(ex));
    }

    private Mono<Void> afterCompletion(ServerWebExchange exchange, long startNanos) {
        HttpStatusCode status = exchange.getResponse().getStatusCode();
        int statusCode = status == null ? 0 : status.value();

        boolean downstreamFailure = statusCode == HttpStatus.BAD_GATEWAY.value()
                || statusCode == HttpStatus.GATEWAY_TIMEOUT.value();
        boolean noRouteFailure = statusCode == HttpStatus.NOT_FOUND.value()
                && exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR) == null;

        if (!downstreamFailure && !noRouteFailure) {
            return Mono.empty();
        }

        return emit(exchange, statusCode, startNanos);
    }

    private int thrownFailureStatus(ServerWebExchange exchange, Throwable ex) {
        int connectivity = DownstreamFailures.statusFor(ex);
        if (connectivity != 0) {
            return connectivity;
        }
        if (ex instanceof ResponseStatusException rse) {
            int statusCode = rse.getStatusCode().value();
            boolean downstreamFailure = statusCode == HttpStatus.BAD_GATEWAY.value()
                    || statusCode == HttpStatus.GATEWAY_TIMEOUT.value();
            boolean noRouteFailure = statusCode == HttpStatus.NOT_FOUND.value()
                    && exchange.getAttribute(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR) == null;
            if (downstreamFailure || noRouteFailure) {
                return statusCode;
            }
        }
        return 0;
    }

    private Mono<Void> emit(ServerWebExchange exchange, int statusCode, long startNanos) {
        double latencyMs = (System.nanoTime() - startNanos) / 1_000_000.0;
        String method = exchange.getRequest().getMethod().name();
        String path = exchange.getRequest().getURI().getRawPath();

        return resolver.resolve(exchange)
                .defaultIfEmpty(EdgeTenantResolver.UNKNOWN)
                .doOnNext(tenant -> emitter.requestCompleted(tenant, method, path, statusCode, latencyMs))
                .then();
    }
}
