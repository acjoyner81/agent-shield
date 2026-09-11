package com.agentshield.mcp;

import com.agentshield.mcp.service.SplunkMcpToolService;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class McpToolConfiguration {
    @Bean
    ToolCallbackProvider enterpriseTools(SplunkMcpToolService splunkMcpToolService) {
        return MethodToolCallbackProvider.builder()
                .toolObjects(splunkMcpToolService)
                .build();
    }
}
