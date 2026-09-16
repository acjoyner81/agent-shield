package com.agentshield.mcp;

import com.agentshield.mcp.service.SplunkMcpToolService;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController
public class McpRpcController {
    private final SplunkMcpToolService toolService;

    public McpRpcController(SplunkMcpToolService toolService) {
        this.toolService = toolService;
    }

    @PostMapping("/rpc")
    public Map<String, Object> handleRpc(@RequestBody Map<String, Object> request) {
        String method = (String) request.get("method");
        Map<String, Object> params = (Map<String, Object>) request.get("params");
        
        if ("tools/call".equals(method)) {
            String toolName = (String) params.get("name");
            Map<String, Object> arguments = (Map<String, Object>) params.get("arguments");
            
            if ("echoTool".equals(toolName)) {
                String message = (arguments != null) ? (String) arguments.getOrDefault("message", "No message provided") : "No arguments provided";
                // Call the actual tool service
                Map<String, Object> result = toolService.echoTool(message);
                return Map.of(
                    "jsonrpc", "2.0",
                    "result", result,
                    "id", request.getOrDefault("id", 1)
                );
            }
        }
        
        return Map.of(
            "jsonrpc", "2.0",
            "error", Map.of("code", -32601, "message", "Method not found"),
            "id", request.getOrDefault("id", 1)
        );
    }
}
