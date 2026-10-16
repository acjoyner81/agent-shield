package com.agentshield.mcp;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.util.Date;
import java.util.List;
import java.util.Map;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.cloud.contract.wiremock.AutoConfigureWireMock;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

/**
 * M-AC-3: every route except /health and /actuator/health verifies issuer and
 * audience. A direct call with no bearer, an expired token, or a wrong audience
 * receives 401, while health stays public.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWireMock(port = 9898)
class McpSecurityTests {

    private static final String ISSUER = "http://localhost:9898/auth";
    private static final String AUDIENCE = "test-audience";
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

    @DynamicPropertySource
    static void jwtProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.security.oauth2.resourceserver.jwt.issuer-uri", () -> ISSUER);
        registry.add("spring.security.oauth2.resourceserver.jwt.audiences", () -> AUDIENCE);
    }

    @org.junit.jupiter.api.BeforeEach
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
    void bareCallWithoutBearerIsRejected() {
        client().post().uri("/mcp/v1")
                .bodyValue(Map.of("jsonrpc", "2.0", "method", "tools/call", "id", 1))
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void expiredTokenIsRejected() throws Exception {
        client().post().uri("/mcp/v1")
                .header("Authorization", "Bearer " + token(expiredClaims()))
                .bodyValue(Map.of("jsonrpc", "2.0", "method", "tools/call", "id", 1))
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void wrongAudienceTokenIsRejected() throws Exception {
        client().post().uri("/mcp/v1")
                .header("Authorization", "Bearer " + token(futureClaims("another-audience")))
                .bodyValue(Map.of("jsonrpc", "2.0", "method", "tools/call", "id", 1))
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void validTokenReachesTheStreamableEndpoint() throws Exception {
        client().post().uri("/mcp/v1")
                .header("Authorization", "Bearer " + token(futureClaims(AUDIENCE)))
                .header("Accept", "application/json, text/event-stream")
                .bodyValue(Map.of(
                        "jsonrpc", "2.0",
                        "id", 1,
                        "method", "initialize",
                        "params", Map.of(
                                "protocolVersion", "2024-11-05",
                                "capabilities", Map.of(),
                                "clientInfo", Map.of("name", "security-test", "version", "1.0.0"))))
                .exchange()
                .expectStatus().isOk();
    }

    @Test
    void healthStaysPublic() {
        client().get().uri("/health").exchange().expectStatus().isOk();
    }

    private static KeyPair generateKeyPair() {
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            return generator.generateKeyPair();
        } catch (Exception e) {
            throw new IllegalStateException("failed to generate test key pair", e);
        }
    }

    private JWTClaimsSet expiredClaims() {
        return new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(List.of(AUDIENCE))
                .subject("tenant_alpha|user_dev_456")
                .issueTime(new Date(System.currentTimeMillis() - 120_000))
                .expirationTime(new Date(System.currentTimeMillis() - 60_000))
                .build();
    }

    private JWTClaimsSet futureClaims(String audience) {
        return new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(List.of(audience))
                .subject("tenant_alpha|user_dev_456")
                .issueTime(new Date())
                .expirationTime(new Date(System.currentTimeMillis() + 300_000))
                .build();
    }

    private String token(JWTClaimsSet claims) throws Exception {
        SignedJWT jwt = new SignedJWT(
                new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(KEY_ID).build(),
                claims);
        jwt.sign(new RSASSASigner(KEY_PAIR.getPrivate()));
        return jwt.serialize();
    }
}