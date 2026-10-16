package com.agentshield.gateway.telemetry;

import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.UnknownHostException;
import java.util.concurrent.TimeoutException;

/**
 * Classifies the exception Spring Cloud Gateway raises when a downstream
 * produced no response. Shared by {@link DownstreamFailureWebExceptionHandler},
 * which turns it into the response status, and {@link EdgeRoutingTelemetryFilter},
 * which turns it into a routing-failure event, so both agree on what a
 * downstream failure is.
 */
final class DownstreamFailures {

    static final int BAD_GATEWAY = 502;
    static final int GATEWAY_TIMEOUT = 504;

    private static final String NETTY_READ_TIMEOUT = "io.netty.handler.timeout.ReadTimeoutException";
    private static final String NETTY_CONNECT_TIMEOUT = "io.netty.channel.ConnectTimeoutException";

    private DownstreamFailures() {
    }

    /**
     * 502 for a connect failure, 504 for a timeout, 0 for anything else. The
     * cause chain is walked because the HTTP client wraps the socket exception.
     */
    static int statusFor(Throwable ex) {
        Throwable cause = ex;
        while (cause != null) {
            String className = cause.getClass().getName();
            // A timeout is checked before ConnectException in the same node:
            // io.netty.channel.ConnectTimeoutException extends ConnectException.
            if (cause instanceof TimeoutException
                    || NETTY_READ_TIMEOUT.equals(className)
                    || NETTY_CONNECT_TIMEOUT.equals(className)) {
                return GATEWAY_TIMEOUT;
            }
            if (cause instanceof UnknownHostException
                    || cause instanceof ConnectException
                    || cause instanceof NoRouteToHostException) {
                return BAD_GATEWAY;
            }
            cause = cause.getCause() == cause ? null : cause.getCause();
        }
        return 0;
    }
}
