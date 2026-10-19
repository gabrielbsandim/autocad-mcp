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

from autocad_mcp import com
from autocad_mcp.server import mcp

results: list[tuple[str, bool, str]] = []


async def call(tool: str, **args):
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
        await call("autocad_status")
        print(await call("new_drawing"))
        test_doc_name = await com.run(lambda: com.doc().Name)
        await call("set_variable", name="INSUNITS", value="6")  # meters

        # Simple floor plan: 5x4 living room and 3x4 bedroom, with names
        await call("create_layer", name="A-AREA", color="green", plottable=False)
        await call("draw_rectangle", corner=[0, 0], width=5, height=4, layer="A-AREA")
        await call("draw_rectangle", corner=[5, 0], width=3, height=4, layer="A-AREA")
        await call("draw_text", text="LIVING", position=[2, 2], height=0.2, layer="TEXTS")
        await call("draw_text", text="BEDROOM", position=[6, 2], height=0.2, layer="TEXTS")
        await call("draw_walls", path=[[0, 0], [8, 0], [8, 4], [0, 4]], thickness=0.15, closed=True, layer="WALLS")
        await call("draw_line", start=[0, -1], end=[8, -1], layer="DIMS")
        await call("draw_circle", center=[10, 2], radius=0.5)
        await call("draw_arc", center=[1, 4], radius=0.8, start_angle_deg=0, end_angle_deg=90, layer="DOORS")
        await call("draw_polyline", points=[[12, 0], [14, 0], [14, 2]], closed=False)
        await call("add_dimension", start=[0, 0], end=[8, 0], text_position=[4, -0.8], kind="linear")
        await call("add_dimension", start=[0, 0], end=[0, 4], text_position=[-0.8, 2], kind="aligned")
        await call("draw_hatch", boundary=[[20, 0], [22, 0], [22, 2], [20, 2]], pattern="ANSI31", scale=0.05)
        await call("draw_text", text="General project notes", position=[0, 6], height=0.15, multiline_width=4)

        # Test block: create the definition over COM and insert it 3 times
        def make_block():
            d = com.doc()
            from autocad_mcp import acad
            b = d.Blocks.Add(acad.vpoint(0, 0), "DOOR_80")
            b.AddLine(acad.vpoint(0, 0), acad.vpoint(0.8, 0))
            b.AddAttribute(0.1, 0, "Type", acad.vpoint(0, -0.2), "TYPE", "D1")
            return "ok"
        await com.run(make_block)
        await call("insert_block", name="DOOR_80", position=[1, 0], attributes={"TYPE": "D1"})
        await call("insert_block", name="DOOR_80", position=[6, 0], attributes={"TYPE": "D2"})
        await call("insert_block", name="DOOR_80", position=[5, 2], rotation_deg=90, attributes={"TYPE": "D1"})

        # Queries
        await call("drawing_summary")
        q = await call("query_entities", types=["text"], text_contains="living")
        await call("query_entities", layers=["A-AREA"], detail=True)
        await call("query_entities", region=[[-1, -2], [9, 5]], limit=5)
        await call("list_blocks")
        await call("list_layers")
        await call("list_layouts")
        await call("list_documents")
        await call("get_selection")
        await call("get_variables", names=["INSUNITS", "CLAYER", "LTSCALE"])
        handle = json.loads(q)["objects"][0]["handle"]
        await call("get_entities", handles=[handle])

        # Areas
        areas = await call("room_areas", layers=["A-AREA"])
        print(areas)
        await call("label_room_areas", layers=["A-AREA"], include_name=False)
        await call("create_area_table", position=[0, -3], layers=["A-AREA"])
        await call("export_areas_csv", file_path=os.path.join(tmp, "areas.csv"), layers=["A-AREA"])

        # Quantities
        print(await call("count_blocks", names=["DOOR*"], group_by_attribute="TYPE"))
        await call("lengths_by_layer", layers=["WALLS", "DIMS"])
        await call("export_quantities_csv", file_path=os.path.join(tmp, "quant.csv"), group_by_attribute="TYPE")

        # Editing
        await call("copy_entities", handles=[handle], offset=[0, 1], times=2)
        await call("move_entities", handles=[handle], offset=[0.1, 0])
        await call("rotate_entities", handles=[handle], base=[2, 2], angle_deg=10)
        await call("scale_entities", handles=[handle], base=[2, 2], factor=1.2)
        await call("mirror_entities", handles=[handle], p1=[0, 10], p2=[1, 10])
        await call("change_properties", handles=[handle], color="red", lineweight_mm=0.25, text="LIVING ROOM")
        await call("change_properties", handles=[handle], linetype="DASHED")
        circ = json.loads(await call("query_entities", types=["circle"]))["objects"][0]["handle"]
        await call("offset_entities", handles=[circ], distance=0.1)
        await call("delete_entities", handles=[circ])
        await call("undo_last")

        # Layers
        await call("set_layer_properties", names=["DIMS"], color="red", lineweight_mm=0.13, locked=True)
        await call("set_current_layer", name="A-AREA")
        await call("rename_layer", old_name="TEXTS", new_name="TEXT_TMP")
        await call("create_layer", name="DOORS_2")
        await call("merge_layers", sources=["DOORS_2"], target="DOORS")
        print(await call("check_layer_standard"))
        print(await call("apply_layer_standard", create_missing=True))

        # Output and maintenance
        print(await call("export_layouts_pdf", layouts=["Layout1"], output_folder=tmp))
        await call("audit_drawing")
        await call("purge_drawing")
        await call("save_drawing", file_path=os.path.join(tmp, "test.dwg"))
        await call("save_drawing", file_path=os.path.join(tmp, "test.dwg"))  # must refuse (already exists)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        if test_doc_name:
            def close():
                for d in com.app().Documents:
                    # after "save as" the test drawing is named test.dwg
                    if d.Name == test_doc_name or d.FullName.startswith(tmp_long):
                        d.Close(False)
                        return "closed"
                return "not found"
            try:
                print("Test drawing:", await com.run(close))
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
