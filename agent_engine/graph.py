"""LangGraph workflow boundary."""

from .state import AgentState


async def run_agent(state: AgentState) -> AgentState:
    """Return state unchanged until the first LangGraph workflow is implemented."""
    return state
