import pytest
from langchain_core.messages import HumanMessage
from agent_engine.graph import build_agent_graph

@pytest.fixture
def compiled_graph():
    """Instantiates and returns a compiled LangGraph workflow instance."""
    return build_agent_graph()

@pytest.mark.asyncio
async def test_agent_graph_direct_reasoning_route(compiled_graph):
    """Verify standard prompts complete without routing through external tools."""
    initial_state = {
        "messages": [HumanMessage(content="Hello, summarize Python async functions.")],
        "tenant_id": "tenant_alpha",
        "next_step": "",
        "tool_output": ""
    }

    final_state = await compiled_graph.ainvoke(initial_state)

    assert final_state["next_step"] == "end"
    assert len(final_state["messages"]) == 2
    assert "Agent Processed" in final_state["messages"][-1].content

@pytest.mark.asyncio
async def test_agent_graph_splunk_tool_routing(compiled_graph):
    """Verify queries mentioning 'splunk' or 'logs' route directly to the FastMCP Splunk node."""
    initial_state = {
        "messages": [HumanMessage(content="Check splunk logs for error status 500")],
        "tenant_id": "tenant_alpha",
        "next_step": "",
        "tool_output": ""
    }

    final_state = await compiled_graph.ainvoke(initial_state)

    # State machine should route to call_mcp_splunk node
    assert final_state["next_step"] == "end"
    assert "MCP Splunk Tool Output" in final_state["messages"][-1].content
    assert final_state["tool_output"] != ""