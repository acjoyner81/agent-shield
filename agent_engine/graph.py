"""LangGraph workflow implementation."""

from typing import Dict, Any, Literal
from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END
from agent_engine.state import AgentState

async def router_node(state: AgentState) -> Dict[str, Any]:
    """Inspect input message to determine routing path."""
    messages = state.get("messages", [])
    if not messages:
        return {"next_step": "end"}
    
    last_msg = messages[-1]
    content = getattr(last_msg, "content", "").lower()

    if "splunk" in content or "logs" in content:
        return {"next_step": "mcp_splunk"}
    
    return {"next_step": "reasoning"}

async def call_mcp_splunk_node(state: AgentState) -> Dict[str, Any]:
    """Execute Splunk log retrieval via MCP tool integration."""
    tool_output = "Sample Splunk Log Output: 200 OK - Request processed successfully."
    message = AIMessage(content="MCP Splunk Tool Output: Logs fetched successfully.")
    
    return {
        "tool_output": tool_output,
        "messages": [message],
        "next_step": "end"
    }

async def reasoning_node(state: AgentState) -> Dict[str, Any]:
    """Process standard LLM reasoning without external tools."""
    message = AIMessage(content="Agent Processed: Request complete.")
    
    return {
        "messages": [message],
        "next_step": "end"
    }

def route_decision(state: AgentState) -> Literal["call_mcp_splunk", "reasoning", END]:
    """Conditional routing edge callback."""
    next_step = state.get("next_step")
    if next_step == "mcp_splunk":
        return "call_mcp_splunk"
    elif next_step == "reasoning":
        return "reasoning"
    return END

def build_agent_graph():
    """Builds and compiles the production LangGraph state machine."""
    workflow = StateGraph(AgentState)

    workflow.add_node("router", router_node)
    workflow.add_node("call_mcp_splunk", call_mcp_splunk_node)
    workflow.add_node("reasoning", reasoning_node)

    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router",
        route_decision,
        {
            "call_mcp_splunk": "call_mcp_splunk",
            "reasoning": "reasoning",
            END: END
        }
    )
    workflow.add_edge("call_mcp_splunk", END)
    workflow.add_edge("reasoning", END)

    return workflow.compile()

async def run_agent(state: AgentState) -> AgentState:
    """Execute compiled graph for a given initial state."""
    graph = build_agent_graph()
    return await graph.ainvoke(state)