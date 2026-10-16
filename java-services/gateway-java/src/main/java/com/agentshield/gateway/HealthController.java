package com.agentshield.gateway;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * G-AC-5: a plain JSON health endpoint that stays up while the Python gateway is
 * down. The platform health probe reads /actuator/health; this is the simple
 * surface operators and the portal can read.
 */
@RestController
public class HealthController {

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP");
    }
}
