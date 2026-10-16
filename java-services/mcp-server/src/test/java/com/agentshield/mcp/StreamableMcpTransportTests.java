package com.agentshield.mcp;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.client.transport.WebClientStreamableHttpTransport;
import io.modelcontextprotocol.spec.McpSchema;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * M-AC-1: the Spring AI streamable transport serves tools/list and tools/call
 * over the configured endpoint /mcp/v1 for exactly the tools the
 * ToolCallbackProvider registers (echoTool, querySplunkLogs).
 *
 * The JWT decoder is stubbed so this test isolates the transport surface; the
 * real issuer/audience verification is covered by McpSecurityTests.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class StreamableMcpTransportTests {

    @LocalServerPort
    private int port;

    @MockitoBean
    private ReactiveJwtDecoder jwtDecoder;

    private McpSyncClient mcpClient;

    @BeforeEach
    void setUp() {
        given(jwtDecoder.decode(anyString())).willReturn(Mono.just(Jwt.withTokenValue("test-token")
                .header("alg", "none")
                .subject("tenant_alpha|user_dev_456")
                .claim("aud", List.of("test-audience"))
                .issuedAt(Instant.now())
                .expiresAt(Instant.now().plusSeconds(300))
                .build()));

        WebClient.Builder webClientBuilder = WebClient.builder()
                .baseUrl("http://localhost:" + port)
                .defaultHeader("Authorization", "Bearer test-token");
        var transport = WebClientStreamableHttpTransport.builder(webClientBuilder)
                .endpoint("/mcp/v1")
                .build();
        this.mcpClient = McpClient.sync(transport).build();
        this.mcpClient.initialize();
    }

    @AfterEach
    void tearDown() {
        if (mcpClient != null) {
            mcpClient.closeGracefully();
        }
    }

    @Test
    @DisplayName("tools/list reports the registered tools over the streamable endpoint")
    void listsRegisteredTools() {
        var names = mcpClient.listTools().tools().stream().map(McpSchema.Tool::name).toList();
        assertThat(names).contains("echoTool", "querySplunkLogs");
        assertThat(names).doesNotContain("validateFirewallRule");
    }

    @Test
    @DisplayName("tools/call for echoTool returns its echo over the streamable endpoint")
    void callsEchoTool() {
        var request = McpSchema.CallToolRequest.builder()
                .name("echoTool")
                .arguments(Map.of("message", "hello"))
                .build();
        var result = mcpClient.callTool(request);
        assertThat(result.isError()).isNotEqualTo(Boolean.TRUE);
        String text = ((McpSchema.TextContent) result.content().get(0)).text();
        assertThat(text).contains("hello");
    }

    @Test
    @DisplayName("an unknown tools/call is a JSON-RPC error and the server stays up")
    void unknownToolErrorsWithoutCrashing() {
        var request = McpSchema.CallToolRequest.builder()
                .name("doesNotExist")
                .arguments(Map.of())
                .build();

        assertThatThrownBy(() -> mcpClient.callTool(request))
                .isInstanceOf(io.modelcontextprotocol.spec.McpError.class);

        // The server is still serving after the failed call.
        var names = mcpClient.listTools().tools().stream().map(McpSchema.Tool::name).toList();
        assertThat(names).contains("echoTool");
    }
}