package com.agentshield.gateway.telemetry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.net.ConnectException;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.cloud.gateway.support.ServerWebExchangeUtils;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

/**
 * G-AC-4: the edge emits one request.completed event for a routing failure and
 * nothing for a request that was actually served. A failure is a 502/504 from a
 * downstream that produced no response, or a 404 the framework answered with no
 * route matched. A 404 on a matched route is a served response, so it is not an
 * edge failure.
 */
class EdgeRoutingTelemetryFilterTest {

    private final EdgeTelemetryEmitter emitter = mock(EdgeTelemetryEmitter.class);
    private final EdgeTenantResolver resolver = mock(EdgeTenantResolver.class);
    private final EdgeRoutingTelemetryFilter filter = new EdgeRoutingTelemetryFilter(emitter, resolver);

    @BeforeEach
    void resolveToTenantAlpha() {
        when(resolver.resolve(any())).thenReturn(Mono.just("tenant_alpha"));
    }

    @Test
    void noRouteFoundEmits() {
        run(exchange(404, false));

        ArgumentCaptor<Integer> status = ArgumentCaptor.forClass(Integer.class);
        verify(emitter).requestCompleted(eq("tenant_alpha"), eq("GET"), eq("/v1/chat/x"), status.capture(), anyDouble());
        assertThat(status.getValue()).isEqualTo(404);
    }

    @Test
    void matchedRouteNotFoundIsServedNotAnEdgeFailure() {
        run(exchange(404, true));

        verifyNoEmission();
    }

    @Test
    void badGatewayEmits() {
        run(exchange(502, false));

        verify(emitter).requestCompleted(any(), any(), any(), eq(502), anyDouble());
    }

    @Test
    void gatewayTimeoutEmits() {
        run(exchange(504, false));

        verify(emitter).requestCompleted(any(), any(), any(), eq(504), anyDouble());
    }

    @Test
    void servedSuccessIsNotAnEdgeFailure() {
        run(exchange(200, false));

        verifyNoEmission();
    }

    @Test
    void unknownTenantStillEmits() {
        when(resolver.resolve(any())).thenReturn(Mono.empty());

        run(exchange(502, false));

        verify(emitter).requestCompleted(eq("unknown"), any(), any(), eq(502), anyDouble());
    }

    // A routing failure that arrives as a thrown exception: the downstream
    // produced no response at all, or the framework answered no route. The
    // exception skips the completion continuation, so the filter classifies it
    // in onErrorResume, emits, then re-propagates for the error handler.

    @Test
    void connectivityFailureThrownEmitsBadGateway() {
        runWithError(exchange(0, false), new ConnectException("Connection refused"));

        verify(emitter).requestCompleted(eq("tenant_alpha"), eq("GET"), eq("/v1/chat/x"), eq(502), anyDouble());
    }

    @Test
    void noRouteFailureThrownEmitsNotFound() {
        runWithError(exchange(0, false), new ResponseStatusException(HttpStatus.NOT_FOUND));

        verify(emitter).requestCompleted(eq("tenant_alpha"), eq("GET"), eq("/v1/chat/x"), eq(404), anyDouble());
    }

    @Test
    void matchedRouteNotFoundThrownIsNotAnEdgeFailure() {
        runWithError(exchange(0, true), new ResponseStatusException(HttpStatus.NOT_FOUND));

        verifyNoEmission();
    }

    @Test
    void unrelatedErrorIsRepropagatedWithNoEmission() {
        IllegalStateException boom = new IllegalStateException("boom");

        Throwable thrown = runWithError(exchange(0, false), boom);

        assertThat(thrown).isSameAs(boom);
        verifyNoEmission();
    }

    private void verifyNoEmission() {
        verify(emitter, never()).requestCompleted(any(), any(), any(), anyInt(), anyDouble());
    }

    private void run(MockServerWebExchange exchange) {
        filter.filter(exchange, ex -> Mono.empty()).block();
    }

    private Throwable runWithError(ServerWebExchange exchange, Throwable error) {
        Throwable[] captured = new Throwable[1];
        StepVerifier.create(filter.filter(exchange, ex -> Mono.error(error)))
                .expectErrorSatisfies(thrown -> captured[0] = thrown)
                .verify();
        return captured[0];
    }

    private MockServerWebExchange exchange(int status, boolean withRoute) {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.method(HttpMethod.GET, "/v1/chat/x").build());
        if (withRoute) {
            exchange.getAttributes().put(ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR, new Object());
        }
        if (status > 0) {
            exchange.getResponse().setStatusCode(HttpStatus.valueOf(status));
        }
        return exchange;
    }
}
