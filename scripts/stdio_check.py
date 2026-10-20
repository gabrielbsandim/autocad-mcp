"""Test the server exactly the way Claude Desktop uses it: a separate process over stdio.

Checks: handshake, tool list, single-step undo and read-only mode. Runs in file mode by default, so it
works on any OS; pass --live on Windows to run it against the CAD that is open (a new blank drawing,
closed without saving).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

LIVE = "--live" in sys.argv
BACKEND = {} if LIVE else {"AUTOCAD_MCP_BACKEND": "file"}


def text(res) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


def server(extra: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "autocad_mcp"],
                                 env={**os.environ, **BACKEND, **extra})


async def objects(s: ClientSession) -> int:
    res = await s.call_tool("describe_drawing", {"sections": ["summary"]})
    return json.loads(text(res))["summary"]["total"]


async def main() -> int:
    failures = 0
    async with stdio_client(server({})) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        tools = (await s.list_tools()).tools
        print("server:", init.server_info.name, init.server_info.version, "| tools:", len(tools))
        untitled = [t.name for t in tools if not t.title]
        failures += bool(untitled)
        print("every tool has a title:", "OK" if not untitled else f"FAILED {untitled}")
        print(text(await s.call_tool("open_drawing", {"action": "new"})))
        before = await objects(s)
        print(text(await s.call_tool("draw", {"items": [
            {"shape": "walls", "path": [[0, 0], [5, 0], [5, 4]], "thickness": 0.15},
            {"shape": "text", "text": "SALA", "position": [2, 2], "height": 0.2},
        ]})))
        after = await objects(s)
        print(text(await s.call_tool("undo", {})))
        await asyncio.sleep(1.5 if LIVE else 0)
        undone = await objects(s)
        ok = after > before and undone == before
        failures += not ok
        print(f"single-step undo: before={before} after={after} after_undo={undone} -> {'OK' if ok else 'FAILED'}")

    async with stdio_client(server({"AUTOCAD_MCP_READONLY": "1"})) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        if not LIVE:
            await s.call_tool("open_drawing", {"action": "new"})
        res = await s.call_tool("draw", {"items": [{"shape": "line", "start": [0, 0], "end": [1, 1]}]})
        ok = res.is_error and "READ-ONLY" in text(res)
        failures += not ok
        print(f"read-only mode blocks writes: {'OK' if ok else 'FAILED'} ({text(res)[:80]})")
        res = await s.call_tool("status", {})
        failures += res.is_error
        print(f"read-only mode allows queries: {'FAILED' if res.is_error else 'OK'}")

    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
