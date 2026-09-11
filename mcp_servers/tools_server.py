"""FastMCP tools for enterprise observability and evaluation."""

import json

from fastmcp import Context, FastMCP

mcp = FastMCP("Enterprise Tool Gateway")


@mcp.tool()
async def query_splunk_logs(
    search_query: str, limit: int = 10, ctx: Context | None = None
) -> str:
    """Execute a Splunk audit search using the local development response."""
    if ctx:
        await ctx.info(f"Executing Splunk search: {search_query}")

    audit_data = [
        {"event_id": "EVT-1029", "status": "200", "latency_ms": 142, "tenant_id": "tenant_alpha"},
        {"event_id": "EVT-1030", "status": "429", "latency_ms": 12, "tenant_id": "tenant_beta"},
    ][: max(0, limit)]
    return json.dumps(
        {"query": search_query, "results_returned": len(audit_data), "events": audit_data}
    )


@mcp.tool()
async def run_llm_judge_eval(
    prompt: str, response: str, ctx: Context | None = None
) -> str:
    """Evaluate relevance, faithfulness, and safety using a local development response."""
    del prompt, response
    if ctx:
        await ctx.info("Running asynchronous LLM-as-a-Judge evaluation pass...")

    return json.dumps(
        {
            "faithfulness_score": 0.94,
            "relevance_score": 0.91,
            "hallucination_detected": False,
            "pii_leak_detected": False,
            "status": "PASSED",
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
