package com.agentshield.gateway.telemetry;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.ReactiveSecurityContextHolder;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * Resolves the tenant label used to tag edge telemetry. This is telemetry only
 * and never authorizes anything: Python remains the single authority for
 * identity. When a request was authenticated the validated JWT carries the same
 * namespaced claim Python reads. On a rejection Spring hands back no Jwt, so the
 * edge does a best effort unverified read of the bearer payload and defaults to
 * {@code unknown}.
 */
@Component
public class EdgeTenantResolver {

    public static final String UNKNOWN = "unknown";

    private final String tenantClaim;
    private final ObjectMapper mapper;

    public EdgeTenantResolver(
            @Value("${spring.security.oauth2.resourceserver.jwt.audiences:https://api.agentshield.local}") String audience,
            ObjectMapper mapper) {
        this.tenantClaim = audience + "/tenant_id";
        this.mapper = mapper;
    }

    /** The namespaced tenant claim, for example https://api.agentshield.local/tenant_id. */
    public String tenantClaim() {
        return tenantClaim;
    }

    public Mono<String> resolve(ServerWebExchange exchange) {
        return ReactiveSecurityContextHolder.getContext()
                .map(SecurityContext::getAuthentication)
                .filter(auth -> auth != null && auth.isAuthenticated())
                .map(Authentication::getPrincipal)
                .filter(Jwt.class::isInstance)
                .cast(Jwt.class)
                .map(this::tenantFromJwt)
                .switchIfEmpty(Mono.fromSupplier(() -> bestEffortFromExchange(exchange)))
                .defaultIfEmpty(UNKNOWN)
                .onErrorReturn(UNKNOWN);
    }

    public String tenantFromJwt(Jwt jwt) {
        Object claim = jwt.getClaim(tenantClaim);
        if (claim == null) {
            claim = jwt.getClaim("tenant_id");
        }
        return claim == null ? UNKNOWN : String.valueOf(claim);
    }

    /** Best effort unverified read of the bearer payload, telemetry only. */
    public String bestEffortFromExchange(ServerWebExchange exchange) {
        return bestEffortFromAuthorization(exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
    }

    public String bestEffortFromAuthorization(String authorization) {
        if (authorization == null) {
            return UNKNOWN;
        }
        String token = authorization.startsWith("Bearer ") ? authorization.substring("Bearer ".length()) : authorization;
        return bestEffortFromToken(token);
    }

    String bestEffortFromToken(String token) {
        try {
            String[] parts = token.split("\\.");
            if (parts.length < 2 || parts[1].isBlank()) {
                return UNKNOWN;
            }
            byte[] payload = Base64.getUrlDecoder().decode(parts[1]);
            JsonNode claims = mapper.readTree(new String(payload, StandardCharsets.UTF_8));
            JsonNode claim = claims.get(tenantClaim);
            if (claim == null) {
                claim = claims.get("tenant_id");
            }
            if (claim == null || claim.isNull()) {
                return UNKNOWN;
            }
            return claim.asText();
        } catch (RuntimeException | java.io.IOException e) {
            return UNKNOWN;
        }
    }
}
