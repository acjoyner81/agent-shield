package com.agentshield.mcp.service;

import java.util.List;
import java.util.Map;

import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

@Service
public class SplunkMcpToolService {

    @Tool(description = "Simple echo tool for end-to-end connectivity verification")
    public Map<String, Object> echoTool(
            @ToolParam(description = "Text to echo back") String message) {
        return Map.of(
                "status", "success",
                "echo", message,
                "timestamp", java.time.Instant.now().toString());
    }

    @Tool(description = "High-performance enterprise log query engine for Splunk integration")

    public Map<String, Object> querySplunkLogs(
            @ToolParam(description = "SPL search query string") String searchQueryParams,
            @ToolParam(description = "Max event records limit") int limit) {

        List<Map<String, Object>> mockEvents = List.of(
                Map.of("eventId", "JAVA-EVT-9001", "status", "200", "latencyMs", 14),
                Map.of("eventId", "JAVA-EVT-9002", "status", "429", "latencyMs", 3));

        List<Map<String, Object>> limitedEvents = mockEvents.subList(
                0, Math.min(Math.max(limit, 0), mockEvents.size()));

        return Map.of(
                "runtime", "Spring Boot 3.4 / Java 21",
                "query", searchQueryParams,
                "recordsCount", limitedEvents.size(),
                "events", limitedEvents);
    }
}