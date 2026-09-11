"""Shared LangGraph state definitions."""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    tenant_id: str
    request_id: str
    prompt: str
    response: str
    tool_output: dict[str, object]
    next_step: str
