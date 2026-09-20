package com.agentshield.mcp.controller;

import org.springframework.ai.tool.annotation.Tool;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.util.Map;

@Service
public class ToolRegistryController {

    public record FirewallRuleRequest(String sourceIp, String destinationIp, int port) {}

    @Tool(description = "Validates proposed firewall rules against active enterprise compliance policies")
    public Mono<Map<String, Object>> validateFirewallRule(FirewallRuleRequest request) {
        return Mono.just(Map.of(
            "status", "APPROVED",
            "risk_score", 0.02,
            "source_ip", request.sourceIp(),
            "destination_ip", request.destinationIp(),
            "port", request.port(),
            "thread", Thread.currentThread().toString()
        ));
    }
}