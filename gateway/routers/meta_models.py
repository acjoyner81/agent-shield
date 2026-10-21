"""Guarded Meta model proxy through Baseten inference."""

import logging
from typing import Annotated, Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config.guardrails.scanner import redact_pii, scan_prompt_injection
from config.settings import settings
from gateway.auth import resolve_active_tenant
from gateway.telemetry import emit_event

router = APIRouter(prefix="/v1/meta", tags=["meta-models"])
logger = logging.getLogger("agentshield.meta_router")

BASETEN_API_BASE_URL = settings.meta_api_base_url.rstrip("/")
BASETEN_API_KEY = settings.baseten_api_key
DEFAULT_MODEL = settings.meta_default_model


class MetaLLMRequest(BaseModel):
    prompt: Optional[str] = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    max_tokens: Optional[int] = 2048

def _content_text(value: Any) -> str:
    """Flatten text content blocks for the existing string based scanner."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(
            part["text"]
            for part in value
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _redact_strings(value: Any) -> tuple[Any, int]:
    """Redact PII from request and response text without logging its value."""
    if isinstance(value, str):
        text, changed = redact_pii(value, settings.meta_pii_mask_patterns)
        return text, int(changed)
    if isinstance(value, list):
        result, count = [], 0
        for item in value:
            clean, n = _redact_strings(item)
            result.append(clean)
            count += n
        return result, count
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            clean, n = _redact_strings(item)
            result[key] = clean
            count += n
        return result, count
    return value, 0


def sanitize_model_output(data: Any) -> Any:
    """Remove private reasoning fields from standard completion responses."""
    if not isinstance(data, dict):
        return data
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for part_name in ("message", "delta"):
                part = choice.get(part_name)
                if isinstance(part, dict):
                    part.pop("reasoning_content", None)
                    part.pop("reasoning", None)
    return data


def _emit_meta_event(
    tenant_id: str, event_type: str, request: Request, data: dict[str, Any]
) -> None:
    """Send a content safe audit event to the asynchronous telemetry queue."""
    try:
        emit_event(
            tenant_id=tenant_id,
            event_type=event_type,
            data=data,
            user_id=getattr(request.state, "user_id", None),
            trace_id=getattr(request.state, "trace_id", None),
            source="agentshield-meta-gateway",
        )
    except Exception:
        logger.exception("Could not enqueue Meta gateway audit event %s", event_type)


@router.post("/chat/completions", include_in_schema=False)
async def meta_chat_completions(
    payload: MetaLLMRequest,
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
    request: Request,
) -> dict[str, object]:
    """Scan, redact, and route a completion through Baseten."""
    user_messages = [
        {"role": "user", "content": payload.prompt}
    ] if payload.prompt else []
    user_messages.extend(
        {
            "role": str(message.get("role", "user")),
            "content": _content_text(message.get("content", "")),
        }
        for message in payload.messages
        if isinstance(message, dict) and message.get("role", "user") == "user"
    )
    text_to_scan = " ".join(message["content"] for message in user_messages)

    if payload.max_tokens is not None and payload.max_tokens > settings.meta_max_tokens_limit:
        raise HTTPException(
            status_code=422,
            detail=f"max_tokens cannot exceed configured limit of {settings.meta_max_tokens_limit}",
        )

    try:
        if settings.meta_prompt_injection_enabled:
            scan_prompt_injection(user_messages)
    except HTTPException:
        logger.warning("Prompt injection blocked for tenant %s", tenant_id)
        _emit_meta_event(
            tenant_id,
            "agentshield.security.prompt_injection_blocked",
            request,
            {"model": payload.model, "level": "WARNING"},
        )
        raise HTTPException(
            status_code=400,
            detail="Prompt rejected by AgentShield ingress guardrails: potential injection detected.",
        )

    if not text_to_scan.strip() and not payload.messages:
        raise HTTPException(status_code=400, detail="A prompt or at least one message is required")
    if not BASETEN_API_KEY:
        raise HTTPException(status_code=503, detail="Baseten inference is not configured")

    request_body = payload.model_dump(exclude_none=True)
    if payload.prompt:
        request_body.pop("prompt", None)
        request_body["messages"] = [
            {"role": "user", "content": payload.prompt},
            *request_body.get("messages", []),
        ]
    if settings.meta_pii_redaction_enabled:
        request_body, redacted_count = _redact_strings(request_body)
    else:
        redacted_count = 0
    if redacted_count:
        _emit_meta_event(
            tenant_id,
            "agentshield.security.pii_redacted",
            request,
            {
                "model": payload.model,
                "level": "INFO",
                "redacted_values": redacted_count,
                "location": "model_input",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=settings.meta_request_timeout_seconds) as client:
            response = await client.post(
                f"{BASETEN_API_BASE_URL}/chat/completions",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {BASETEN_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            result_data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Baseten inference returned HTTP %s", exc.response.status_code)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="Baseten inference request failed",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Could not connect to Baseten inference: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Model inference service is currently unavailable.",
        ) from exc
    except ValueError as exc:
        logger.error("Baseten returned an invalid JSON response")
        raise HTTPException(status_code=502, detail="Model inference returned an invalid response") from exc

    if settings.meta_pii_redaction_enabled:
        result_data, output_redactions = _redact_strings(result_data)
    else:
        output_redactions = 0
    if output_redactions:
        _emit_meta_event(
            tenant_id,
            "agentshield.security.pii_redacted",
            request,
            {
                "model": payload.model,
                "level": "INFO",
                "redacted_values": output_redactions,
                "location": "model_output",
            },
        )
    _emit_meta_event(
        tenant_id,
        "agentshield.meta.model_request_completed",
        request,
        {"model": payload.model, "level": "INFO", "status_code": 200},
    )
    sanitized_output = (
        sanitize_model_output(result_data)
        if settings.meta_sanitize_reasoning_content
        else result_data
    )
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "model_output": sanitized_output,
    }


@router.get("/models", include_in_schema=False)
async def list_meta_models(
    tenant_id: Annotated[str, Depends(resolve_active_tenant)],
) -> dict[str, list[str]]:
    """List the model currently configured for the Meta proxy."""
    return {"models": [DEFAULT_MODEL]}
