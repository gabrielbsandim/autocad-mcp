"""End-to-end test with AutoCAD running.

Creates a NEW blank drawing, exercises the tools through the MCP server itself
and closes that drawing without saving. It does not touch any other open drawing.

    .venv\\Scripts\\python.exe scripts\\live_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback

from autocad_mcp import cad
from autocad_mcp.server import mcp

results: list[tuple[str, bool, str]] = []


async def call(tool: str, /, **args):
    try:
        out = await mcp.call_tool(tool, args)
        text = _text(out)
        failed = _is_error(out)
        results.append((tool, not failed, text[:300]))
        return text
    except Exception as exc:  # noqa: BLE001
        results.append((tool, False, f"{type(exc).__name__}: {exc}"))
        return None


def _text(out) -> str:
    content = getattr(out, "content", out)
    if isinstance(content, tuple):
        content = content[0]
    parts = []
    for c in content if isinstance(content, list) else [content]:
        parts.append(getattr(c, "text", str(c)))
    return "\n".join(parts)


def _is_error(out) -> bool:
    return bool(getattr(out, "is_error", False) or getattr(out, "isError", False))


async def main() -> int:
    test_doc_name = None
    tmp = tempfile.mkdtemp(prefix="acadmcp_")
    tmp_long = os.path.realpath(tmp)
    tmp = tmp_long
    try:
        await call("status")
        print(await call("open_drawing", action="new"))
        test_doc_name = await cad.run(lambda: cad.doc().Name)
        await call("set_variable", name="INSUNITS", value="6")

        await call("manage_layer", action="create", names=["A-AREA"], color="green", plottable=False)
        await call("draw", items=[
            {"shape": "rectangle", "corner": [0, 0], "width": 5, "height": 4, "layer": "A-AREA"},
            {"shape": "rectangle", "corner": [5, 0], "width": 3, "height": 4, "layer": "A-AREA"},
            {"shape": "text", "text": "LIVING", "position": [2, 2], "height": 0.2, "layer": "TEXTS"},
            {"shape": "text", "text": "BEDROOM", "position": [6, 2], "height": 0.2, "layer": "TEXTS"},
            {"shape": "walls", "path": [[0, 0], [8, 0], [8, 4], [0, 4]], "thickness": 0.15, "closed": True,
             "layer": "WALLS"},
            {"shape": "line", "start": [0, -1], "end": [8, -1], "layer": "DIMS"},
            {"shape": "circle", "center": [10, 2], "radius": 0.5},
            {"shape": "arc", "center": [1, 4], "radius": 0.8, "start_angle_deg": 0, "end_angle_deg": 90,
             "layer": "DOORS"},
            {"shape": "polyline", "points": [[12, 0], [14, 0], [14, 2]]},
            {"shape": "dimension", "start": [0, 0], "end": [8, 0], "text_position": [4, -0.8], "kind": "linear"},
            {"shape": "dimension", "start": [0, 0], "end": [0, 4], "text_position": [-0.8, 2]},
            {"shape": "hatch", "boundary": [[20, 0], [22, 0], [22, 2], [20, 2]], "pattern": "ANSI31", "scale": 0.05},
            {"shape": "text", "text": "General project notes", "position": [0, 6], "height": 0.15,
             "multiline_width": 4},
        ])

        def make_block():
            d = cad.doc()
            b = d.Blocks.Add(cad.point(0, 0), "DOOR_80")
            b.AddLine(cad.point(0, 0), cad.point(0.8, 0))
            b.AddAttribute(0.1, 0, "Type", cad.point(0, -0.2), "TYPE", "D1")
            return "ok"
        await cad.run(make_block)
        await call("draw", items=[
            {"shape": "block", "name": "DOOR_80", "position": [1, 0], "attributes": {"TYPE": "D1"}},
            {"shape": "block", "name": "DOOR_80", "position": [6, 0], "attributes": {"TYPE": "D2"}},
            {"shape": "block", "name": "DOOR_80", "position": [5, 2], "rotation_deg": 90, "attributes": {"TYPE": "D1"}},
        ])

        await call("describe_drawing", sections=["summary", "layers", "blocks", "layouts", "variables"],
                   variables=["INSUNITS", "CLAYER", "LTSCALE"])
        q = await call("find_entities", types=["text"], text_contains="living")
        await call("find_entities", layers=["A-AREA"], detail=True)
        await call("find_entities", region=[[-1, -2], [9, 5]], limit=5)
        handle = json.loads(q)["objects"][0]["handle"]
        await call("find_entities", handles=[handle])

        print(await call("room_areas", layers=["A-AREA"]))
        await call("label_rooms", layers=["A-AREA"])
        await call("label_rooms", layers=["A-AREA"])
        await call("place_table", report="rooms", position=[0, -3], layers=["A-AREA"])
        await call("export_csv", report="rooms", file_path=os.path.join(tmp, "areas.csv"), layers=["A-AREA"])

        print(await call("takeoff", names=["DOOR*"], group_by_attribute="TYPE"))
        await call("takeoff", kind="lengths", layers=["WALLS", "DIMS"])
        await call("place_table", report="blocks", position=[20, -3], group_by_attribute="TYPE")
        await call("export_csv", report="blocks", file_path=os.path.join(tmp, "quant.csv"), group_by_attribute="TYPE")

        await call("transform", handles=[handle], operation="copy", offset=[0, 1], times=2)
        await call("transform", handles=[handle], operation="move", offset=[0.1, 0])
        await call("transform", handles=[handle], operation="rotate", base=[2, 2], angle_deg=10)
        await call("transform", handles=[handle], operation="scale", base=[2, 2], factor=1.2)
        await call("transform", handles=[handle], operation="mirror", p1=[0, 10], p2=[1, 10])
        await call("change_properties", handles=[handle], color="red", lineweight_mm=0.25, text="LIVING ROOM")
        await call("change_properties", handles=[handle], linetype="DASHED")
        circ = json.loads(await call("find_entities", types=["circle"]))["objects"][0]["handle"]
        await call("transform", handles=[circ], operation="offset", distance=0.1)
        await call("delete_entities", handles=[circ])
        await call("undo")

        await call("manage_layer", action="update", names=["DIMS"], color="red", lineweight_mm=0.13, locked=True)
        await call("manage_layer", action="set_current", names=["A-AREA"])
        await call("manage_layer", action="rename", names=["TEXTS"], new_name="TEXT_TMP")
        await call("manage_layer", action="create", names=["DOORS_2"])
        await call("merge_layers", sources=["DOORS_2"], target="DOORS")
        print(await call("check_layer_standard"))
        print(await call("apply_layer_standard", create_missing=True))

        print(await call("export_pdf", layouts=["Layout1"], output_folder=tmp))
        await call("maintain_drawing", action="audit")
        await call("maintain_drawing", action="purge")
        await call("save_drawing", file_path=os.path.join(tmp, "test.dwg"))
        await call("save_drawing", file_path=os.path.join(tmp, "test.dwg"))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        if test_doc_name:
            def close():
                for d in cad.app().Documents:
                    # after "save as" the test drawing is named test.dwg
                    if d.Name == test_doc_name or d.FullName.startswith(tmp_long):
                        d.Close(False)
                        return "closed"
                return "not found"
            try:
                print("Test drawing:", await cad.run(close))
            except Exception as exc:  # noqa: BLE001
                print("Failed to close the test drawing:", exc)

    print(f"\nFiles created in {tmp}: {os.listdir(tmp)}")
    print("\n=== RESULT ===")
    expected_fail = {"save_drawing#2"}
    seen: dict[str, int] = {}
    bad = 0
    for name, ok, msg in results:
        seen[name] = seen.get(name, 0) + 1
        key = f"{name}#{seen[name]}"
        flag = "OK " if ok else ("ok*" if key in expected_fail else "ERR")
        if not ok and key not in expected_fail:
            bad += 1
        print(f"{flag} {name}: {msg.splitlines()[0] if msg else ''}"[:200])
    print(f"\n{len(results)} calls, {bad} unexpected error(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
