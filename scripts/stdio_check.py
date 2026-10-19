"""Test the server exactly the way Claude Desktop uses it: a separate process over stdio.

Checks: handshake, tool list, single-step undo and read-only mode.
Works on a new blank drawing and closes it without saving.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PY = sys.executable


def text(res) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


async def session(env_extra: dict[str, str]):
    env = {**os.environ, **env_extra}
    params = StdioServerParameters(command=PY, args=["-m", "autocad_mcp"], env=env)
    return stdio_client(params)


async def main() -> int:
    import win32com.client

    acad = win32com.client.GetActiveObject("AutoCAD.Application.25")
    failures = 0

    async with stdio_client(StdioServerParameters(command=PY, args=["-m", "autocad_mcp"], env=dict(os.environ))) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", init.server_info.name, init.server_info.version)
            tools = (await s.list_tools()).tools
            print("tools:", len(tools))
            print(text(await s.call_tool("new_drawing", {})))
            name = acad.ActiveDocument.Name
            ms = acad.ActiveDocument.ModelSpace
            before = ms.Count
            print(text(await s.call_tool("draw_walls", {"path": [[0, 0], [5, 0], [5, 4]], "thickness": 0.15})))
            after = ms.Count
            print(text(await s.call_tool("undo_last", {})))
            await asyncio.sleep(1.5)
            undone = ms.Count
            ok = after > before and undone == before
            failures += not ok
            print(f"single-step undo: before={before} after={after} after_undo={undone} -> {'OK' if ok else 'FAILED'}")

    async with stdio_client(StdioServerParameters(command=PY, args=["-m", "autocad_mcp"],
                                                  env={**os.environ, "AUTOCAD_MCP_READONLY": "1"})) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("draw_line", {"start": [0, 0], "end": [1, 1]})
            ok = res.is_error and "READ-ONLY" in text(res) and ms.Count == before
            failures += not ok
            print(f"read-only mode blocks writes: {'OK' if ok else 'FAILED'} ({text(res)[:80]}...)")
            res = await s.call_tool("drawing_summary", {})
            ok = not res.is_error
            failures += not ok
            print(f"read-only mode allows queries: {'OK' if ok else 'FAILED'}")

    for d in list(acad.Documents):
        if d.Name == name:
            d.Close(False)
    print("test drawing closed;", "failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
