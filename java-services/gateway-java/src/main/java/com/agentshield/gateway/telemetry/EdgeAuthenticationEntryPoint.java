package com.agentshield.gateway.telemetry;

import org.springframework.http.HttpStatus;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.server.ServerAuthenticationEntryPoint;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * G-AC-3: an authentication rejection at the edge emits one authz_failure event
 * to telemetry:queue (source agentshield-gateway-java, tenant from the token
 * when present, unknown otherwise), then answers 401. The emit is best effort
 * and never delays or changes the 401.
 */
@Component
public class EdgeAuthenticationEntryPoint implements ServerAuthenticationEntryPoint {

    private final EdgeTelemetryEmitter emitter;
    private final EdgeTenantResolver resolver;

    public EdgeAuthenticationEntryPoint(EdgeTelemetryEmitter emitter, EdgeTenantResolver resolver) {
        this.emitter = emitter;
        this.resolver = resolver;
    }

    @Override
    public Mono<Void> commence(ServerWebExchange exchange, AuthenticationException ex) {
        // Spring hands back no Jwt on a rejection, so the tenant is a best effort
        // read of the bearer payload. It is telemetry only, never authorization.
        emitter.authzFailure(resolver.bestEffortFromExchange(exchange));
        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
        return exchange.getResponse().setComplete();
    }
}
