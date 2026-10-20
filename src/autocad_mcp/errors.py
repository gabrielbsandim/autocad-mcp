from mcp.server.mcpserver.exceptions import ToolError


class AcadError(ToolError):
    """Error with a friendly message, ready for Claude to pass on to the user. Subclassing ToolError
    makes the SDK hand the message to the model instead of a bare "Error executing tool"."""
