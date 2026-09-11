package com.agentshield.mcp;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.reactive.server.WebTestClient;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class GatewayApplicationTests {

    @LocalServerPort
    private int port;

    private WebTestClient webTestClient;

    @BeforeEach
    void setUp() {
        this.webTestClient = WebTestClient.bindToServer()
                .baseUrl("http://localhost:" + port)
                .build();
    }

    @Test
    @DisplayName("Should route valid request to FastAPI backend with X-Tenant-API-Key")
    void testRouteToFastApi_Success() {
        webTestClient.post()
                .uri("/v1/chat/completions")
                .header("X-Tenant-API-Key", "key_alpha_123")
                .bodyValue("{\"prompt\": \"Hello Agent Engine\"}")
                .exchange()
                .expectStatus().isOk();
    }

    @Test
    @DisplayName("Should trigger HTTP 429 when tenant rate limits are breached")
    void testRateLimiter_Exceeded() {
        // Repeated requests to exceed mock rate limit
        for (int i = 0; i < 10; i++) {
            webTestClient.post()
                    .uri("/v1/chat/completions")
                    .header("X-Tenant-API-Key", "key_beta_456")
                    .bodyValue("{\"prompt\": \"Rapid fire request\"}")
                    .exchange();
        }

        // Verify gateway enforces Too Many Requests
        webTestClient.post()
                .uri("/v1/chat/completions")
                .header("X-Tenant-API-Key", "key_beta_456")
                .bodyValue("{\"prompt\": \"Over limit request\"}")
                .exchange()
                .expectStatus().isEqualTo(HttpStatus.TOO_MANY_REQUESTS);
    }
}