package com.agentshield.gateway.config;

import java.util.function.Predicate;

import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;

public final class AudienceValidator implements OAuth2TokenValidator<Jwt> {
    private final String audience;
    private final OAuth2Error error = new OAuth2Error("invalid_token", "The required audience is missing", null);

    public AudienceValidator(String audience) {
        this.audience = audience;
    }

    @Override
    public OAuth2TokenValidatorResult validate(Jwt token) {
        Predicate<String> matchesAudience = value -> value.equals(audience);
        return token.getAudience().stream().anyMatch(matchesAudience)
                ? OAuth2TokenValidatorResult.success()
                : OAuth2TokenValidatorResult.failure(error);
    }
}