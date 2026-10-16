package com.agentshield.gateway;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;

import java.io.IOException;
import java.net.ServerSocket;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.util.Date;

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
 * G-AC-4/G-AC-5 at the edge: a downstream that is not listening at all (a
 * refused connection, the same shape as a stopped gateway-python) must reach the
 * client as 502, not the framework's default 500, and must emit exactly one
 * request.completed. The 502 the telemetry filter keys on only exists if the
 * error handler rewrites the raw ConnectException first.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWireMock(port = 9898)
class DownstreamUnreachableTests {

    private static final String ISSUER = "http://localhost:9898/auth";
    private static final String AUDIENCE = "test-audience";
    private static final String TENANT_CLAIM = AUDIENCE + "/tenant_id";
    private static final String KEY_ID = "test-key-1";

    /** A port nothing listens on: bind, read it, close, so connect() is refused. */
    private static final int DEAD_PORT = freePort();

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
        registry.add("PYTHON_GATEWAY_URL", () -> "http://127.0.0.1:" + DEAD_PORT);
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

    @Test
    void unreachableDownstreamBecomesBadGatewayAndEmitsOnce() {
        WebTestClient.bindToServer()
                .baseUrl("http://localhost:" + port)
                .responseTimeout(java.time.Duration.ofSeconds(60))
                .build()
                .get().uri("/v1/chat/x")
                .header("Authorization", "Bearer " + token(claims(AUDIENCE, "tenant_alpha")))
                .exchange()
                .expectStatus().isEqualTo(502);

        verifyEventually(() -> verify(emitter)
                .requestCompleted(eq("tenant_alpha"), eq("GET"), eq("/v1/chat/x"), eq(502), anyDouble()));
    }

    /** The routing event lands after the response is committed, so poll briefly. */
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

    private static int freePort() {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
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
    private JWTClaimsSet claims(String audience, String tenant) {
        return new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(audience)
                .subject(tenant + "|user_dev_456")
                .claim(TENANT_CLAIM, tenant)
                .expirationTime(new Date(System.currentTimeMillis() + 3_600_000L))
                .build();
    }

    private String token(JWTClaimsSet claims) {
        try {
            SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(KEY_ID).build(), claims);
            jwt.sign(new RSASSASigner(KEY_PAIR.getPrivate()));
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
