"""LangGraph workflow boundary."""

from typing import Dict, Any
from langchain_core.messages import AIMessage
from .state import AgentState

class DummyGraph:
    async def ainvoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
        content = state["messages"][0].content if state.get("messages") else ""
        if "splunk" in content.lower() or "logs" in content.lower():
            state["next_step"] = "end"
            state["tool_output"] = "Sample Splunk Log Output"
            state["messages"].append(AIMessage(content="MCP Splunk Tool Output: Logs fetched successfully."))
        else:
            state["next_step"] = "end"
            state["messages"].append(AIMessage(content="Agent Processed: Request complete."))
        return state

def build_agent_graph():
    """Returns the compiled LangGraph workflow instance."""
    return DummyGraph()

async def run_agent(state: AgentState) -> AgentState:
    """Return state unchanged until the first LangGraph workflow is implemented."""
    return state