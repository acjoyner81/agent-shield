# Java Gateway

Spring Cloud Gateway runs on port `8080` and provides the Java edge route layer.

Routes:

- `/v1/chat/**` -> FastAPI gateway at `http://localhost:8000`, protected by the Redis request-rate limiter.
- `/mcp/**` -> Java MCP service at `http://localhost:8081`.

Start locally with Java 21 and Maven:

```bash
mvn spring-boot:run
```

Redis must be available at `localhost:6379` for `RequestRateLimiter`.
