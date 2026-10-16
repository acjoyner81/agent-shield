package com.agentshield.gateway.telemetry;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.ConnectException;
import java.net.UnknownHostException;
import java.util.concurrent.TimeoutException;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ServerWebExchange;

import reactor.test.StepVerifier;

/**
 * G-AC-4/G-AC-5: a downstream the edge cannot reach is a 502 (connect) or 504
 * (timeout), not the framework default 500, so the routing signal the filter
 * keys on survives. Anything the handler does not own is re-emitted for the
 * default handler, so non-connectivity errors keep their normal treatment.
 */
class DownstreamFailureWebExceptionHandlerTest {

    private final DownstreamFailureWebExceptionHandler handler = new DownstreamFailureWebExceptionHandler();

    private static ServerWebExchange exchange() {
        return MockServerWebExchange.from(MockServerHttpRequest.get("/v1/chat/x").build());
    }

    @Test
    void unresolvedHostIsBadGateway() {
        ServerWebExchange exchange = exchange();

        StepVerifier.create(handler.handle(exchange, new UnknownHostException("gateway-python")))
                .verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.BAD_GATEWAY);
    }

    @Test
    void refusedConnectionIsBadGateway() {
        ServerWebExchange exchange = exchange();

        StepVerifier.create(handler.handle(exchange, new ConnectException("Connection refused")))
                .verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.BAD_GATEWAY);
    }

    @Test
    void timeoutIsGatewayTimeout() {
        ServerWebExchange exchange = exchange();

        StepVerifier.create(handler.handle(exchange, new TimeoutException("read timed out")))
                .verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.GATEWAY_TIMEOUT);
    }

    @Test
    void socketFailureIsFoundInTheCauseChain() {
        ServerWebExchange exchange = exchange();
        Throwable wrapped = new RuntimeException("client failed",
                new ConnectException("Connection refused"));

        StepVerifier.create(handler.handle(exchange, wrapped)).verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.BAD_GATEWAY);
    }

    @Test
    void unrelatedErrorIsLeftToTheDefaultHandler() {
        ServerWebExchange exchange = exchange();
        IllegalStateException boom = new IllegalStateException("boom");

        StepVerifier.create(handler.handle(exchange, boom))
                .expectErrorMatches(error -> error == boom)
                .verify();

        assertThat(exchange.getResponse().getStatusCode()).isNull();
    }
}
