package com.agentshield.mcp;

import com.agentshield.mcp.service.SplunkMcpToolService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class SplunkMcpToolServiceTest {

    private SplunkMcpToolService splunkMcpToolService;

    @BeforeEach
    void setUp() {
        splunkMcpToolService = new SplunkMcpToolService();
    }

    @Test
    @DisplayName("Should query Splunk logs and return structured response map")
    void testQuerySplunkLogsSuccess() {
        String searchQuery = "index=main status=429";
        int limit = 10;

        Map<String, Object> response = splunkMcpToolService.querySplunkLogs(searchQuery, limit);

        assertNotNull(response);
        assertEquals("Spring Boot 3.4 / Java 21", response.get("runtime"));
        assertEquals(searchQuery, response.get("query"));

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> events = (List<Map<String, Object>>) response.get("events");
        assertFalse(events.isEmpty());
        assertEquals("JAVA-EVT-9001", events.get(0).get("eventId"));
    }
}
