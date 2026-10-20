from typing import Any

from mcp.types import ToolAnnotations

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

_HINTS = {
    READ: {"read_only_hint": True, "destructive_hint": False},
    WRITE: {"read_only_hint": False, "destructive_hint": False},
    DESTRUCTIVE: {"read_only_hint": False, "destructive_hint": True},
}


def spec(title: str, kind: str, description: str | None = None) -> dict[str, Any]:
    """Tool title and the hints Claude shows in permission prompts. Nothing here reaches the network."""
    out: dict[str, Any] = {
        "title": title,
        "annotations": ToolAnnotations(title=title, open_world_hint=False, **_HINTS[kind]),
    }
    if description:
        out["description"] = description
    return out
