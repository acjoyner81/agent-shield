package com.agentshield.gateway.telemetry;

import java.nio.charset.StandardCharsets;

import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebExceptionHandler;

import reactor.core.publisher.Mono;

/**
 * G-AC-4/G-AC-5: a downstream the edge cannot reach is a routing failure and
 * must reach the client as 502 (connect failure) or 504 (timeout), not the
 * framework's default 500. Spring Cloud Gateway raises the raw client exception
 * (an unresolvable host, a refused connection), which the stock error handler
 * maps to 500, so the client saw a 500 where the contract promises a 502.
 *
 * Runs at order -2, ahead of the auto configured error handler (~-1), so it sees
 * the exception first; any error it does not own is re-emitted and left to the
 * default handler, so this only changes downstream-connectivity outcomes.
 */
@Component
@Order(DownstreamFailureWebExceptionHandler.ORDER)
public class DownstreamFailureWebExceptionHandler implements WebExceptionHandler {

    /** Ahead of the auto configured error handler (~-1) so it sees the error first. */
    public static final int ORDER = -2;

    @Override
    public Mono<Void> handle(ServerWebExchange exchange, Throwable ex) {
        int statusCode = DownstreamFailures.statusFor(ex);
        if (statusCode == 0 || exchange.getResponse().isCommitted()) {
            // Not a downstream connectivity failure (or the response already
            // started): leave it to the default handler unchanged.
            return Mono.error(ex);
        }

        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(HttpStatus.valueOf(statusCode));
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        DataBuffer buffer = response.bufferFactory()
                .wrap(("{\"error\":\"" + statusCode + "\"}").getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }
}
