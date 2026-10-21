FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY config ./config
COPY agent_shield_config.yaml ./agent_shield_config.yaml
COPY gateway ./gateway
COPY docs/help-center ./docs/help-center
COPY evals ./evals
COPY observability ./observability
COPY agent_engine ./agent_engine
COPY mcp_servers ./mcp_servers

EXPOSE 8000
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
