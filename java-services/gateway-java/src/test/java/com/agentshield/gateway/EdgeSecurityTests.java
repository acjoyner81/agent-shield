package com.agentshield.gateway;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.util.Date;
import java.util.List;

import com.agentshield.gateway.telemetry.EdgeTelemetryEmitter;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.cloud.contract.wiremock.AutoConfigureWireMock;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.reactive.server.WebTestClient;

/**
 * G-AC-1: every route except the health endpoints verifies issuer and audience,
 * and no principal header is added for a downstream to trust. G-AC-3: a refused
 * token emits one authz_failure event (tenant best effort, unknown otherwise)
 * before the 401. G-AC-4: only routing failures are emitted; a request a
 * downstream served, even a 404, is counted once by Python instead.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWireMock(port = 9898)
class EdgeSecurityTests {

    private static final String ISSUER = "http://localhost:9898/auth";
    private static final String AUDIENCE = "test-audience";
    private static final String TENANT_CLAIM = AUDIENCE + "/tenant_id";
    private static final String KEY_ID = "test-key-1";

    private static final KeyPair KEY_PAIR = generateKeyPair();
    private static final RSAKey RSA_KEY = new RSAKey.Builder((RSAPublicKey) KEY_PAIR.getPublic())
            .keyID(KEY_ID)
            .algorithm(JWSAlgorithm.RS256)
            .build();

    @LocalServerPort
    private int port;

    @Autowired
    private WireMockServer wireMockServer;

    @MockitoBean
    private EdgeTelemetryEmitter emitter;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.security.oauth2.resourceserver.jwt.issuer-uri", () -> ISSUER);
        registry.add("spring.security.oauth2.resourceserver.jwt.audiences", () -> AUDIENCE);
        // The /v1/chat and /api/v1 routes proxy Python; point them at WireMock so a
        // served request and a failing downstream are both deterministic here.
        registry.add("PYTHON_GATEWAY_URL", () -> "http://localhost:9898");
    }

    @BeforeEach
    void stubIssuerMetadata() {
        wireMockServer.stubFor(get(urlEqualTo("/auth/.well-known/openid-configuration"))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody("{\"issuer\": \"" + ISSUER + "\","
                                + "\"jwks_uri\": \"" + ISSUER + "/jwks\"}")));
        wireMockServer.stubFor(get(urlEqualTo("/auth/jwks"))
                .willReturn(aResponse()
                        .withHeader("Content-Type", "application/json")
                        .withBody(new JWKSet(RSA_KEY.toPublicJWK()).toString())));
    }

    private WebTestClient client() {
        return WebTestClient.bindToServer()
                .baseUrl("http://localhost:" + port)
                .responseTimeout(java.time.Duration.ofSeconds(60))
                .build();
    }

    @Test
    void bareCallWithoutBearerIsRejectedAndEmitsUnknown() {
        client().get().uri("/v1/chat/x")
                .exchange()
                .expectStatus().isUnauthorized();

        verify(emitter).authzFailure("unknown");
    }

    @Test
    void expiredTokenIsRejected() {
        client().get().uri("/v1/chat/x")
                .header("Authorization", "Bearer " + token(claims(AUDIENCE, "tenant_alpha", -1)))
                .exchange()
                .expectStatus().isUnauthorized();

        verify(emitter).authzFailure("tenant_alpha");
    }

    @Test
    void wrongAudienceIsRejectedAndTenantReadFromToken() {
        client().get().uri("/v1/chat/x")
                .header("Authorization", "Bearer " + token(claims("another-audience", "tenant_alpha", 0)))
                .exchange()
                .expectStatus().isUnauthorized();

        verify(emitter).authzFailure("tenant_alpha");
    }

    @Test
    void validTokenReachesTheDownstreamAndServedRequestIsNotCounted() {
        wireMockServer.stubFor(get(urlEqualTo("/v1/chat/ok"))
                .willReturn(aResponse().withStatus(200).withBody("served")));

        client().get().uri("/v1/chat/ok")
                .header("Authorization", "Bearer " + token(claims(AUDIENCE, "tenant_alpha", 0)))
                .exchange()
                .expectStatus().isOk()
                .expectBody(String.class).isEqualTo("served");

        verify(emitter, never()).requestCompleted(any(), any(), any(), anyInt(), anyDouble());
    }

    @Test
    void downstreamFailureEmitsRequestCompleted() {
        wireMockServer.stubFor(get(urlEqualTo("/v1/chat/down"))
                .willReturn(aResponse().withStatus(502)));

        client().get().uri("/v1/chat/down")
                .header("Authorization", "Bearer " + token(claims(AUDIENCE, "tenant_alpha", 0)))
                .exchange()
                .expectStatus().isEqualTo(502);

        verifyEventually(() -> verify(emitter)
                .requestCompleted(eq("tenant_alpha"), eq("GET"), eq("/v1/chat/down"), eq(502), anyDouble()));
    }

    /**
     * The routing event is emitted on the reactive continuation after the response
     * is committed, so the client can observe the 502 before the emitter call lands.
     */
    private void verifyEventually(Runnable verification) {
        long deadline = System.currentTimeMillis() + 2_000;
        while (true) {
            try {
                verification.run();
                return;
            } catch (AssertionError e) {
                if (System.currentTimeMillis() > deadline) {
                    throw e;
                }
                try {
                    Thread.sleep(25);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw e;
                }
            }
        }
    }

    @Test
    void healthIsPublic() {
        client().get().uri("/health")
                .exchange()
                .expectStatus().isOk()
                .expectBody().jsonPath("$.status").isEqualTo("UP");
    }

    private static KeyPair generateKeyPair() {
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            return generator.generateKeyPair();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** A claim set that expires in {@code minutesFromNow} (negative for expired). */
    private JWTClaimsSet claims(String audience, String tenant, int minutesFromNow) {
        return new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(List.of(audience))
                .subject(tenant + "|user_dev_456")
                .claim(TENANT_CLAIM, tenant)
                .issueTime(new Date(System.currentTimeMillis() - 300_000))
                .expirationTime(new Date(System.currentTimeMillis() + minutesFromNow * 60_000L))
                .build();
    }

    private String token(JWTClaimsSet claims) {
        try {
            SignedJWT jwt = new SignedJWT(
                    new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(KEY_ID).build(),
                    claims);
            jwt.sign(new RSASSASigner(KEY_PAIR.getPrivate()));
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
