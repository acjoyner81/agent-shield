# Java Gateway

Spring Cloud Gateway runs on port `8080` and provides the Java edge route layer.

Routes:

- `/api/v1/**` and `/v1/chat/**` -> FastAPI gateway at `http://localhost:8000`. Rate limiting is Python's tenant token bucket; the edge holds no budget of its own.
- `/mcp/**` -> Java MCP service at `http://localhost:8081`.
- `/**` -> the portal frontend at `http://localhost:80`.

Every request except `/health` and `/actuator/health` is verified against the Auth0 issuer and audience. The edge emits telemetry only for what happens at its own layer: refused tokens and routing failures. It writes the 0008 CloudEvents envelope to `telemetry:queue` under source `agentshield-gateway-java`.

Start locally with Java 21 and Maven:

```bash
mvn spring-boot:run
```

Redis must be available at `localhost:6379` for the edge telemetry emitter and the actuator Redis health indicator.
