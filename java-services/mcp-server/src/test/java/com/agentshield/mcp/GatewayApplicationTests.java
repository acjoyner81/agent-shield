package com.agentshield.mcp;

import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import io.modelcontextprotocol.client.transport.WebFluxSseClientTransport;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.web.reactive.function.client.WebClient;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class GatewayApplicationTests {

    @LocalServerPort
    private int port;

    private McpSyncClient mcpClient;

    @BeforeEach
    void setUp() {
        WebClient.Builder webClientBuilder = WebClient.builder()
                .baseUrl("http://localhost:" + port);
        var transport = WebFluxSseClientTransport.builder(webClientBuilder).build();
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
    @DisplayName("Should list registered tools over MCP SSE")
    void testMcpEndpoint() {
        var toolsResult = mcpClient.listTools();
        assertThat(toolsResult.tools()).isNotEmpty();
    }
}