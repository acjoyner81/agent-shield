"""MCP client boundary used by LangGraph nodes."""


class MCPClient:
    def __init__(self, server_script: str = "mcp_servers/tools_server.py") -> None:
        self.server_script = server_script

    async def execute_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Invoke a governed MCP tool; transport wiring is the next implementation step."""
        return {"tool": tool_name, "arguments": arguments, "status": "not_implemented"}
