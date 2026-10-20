"""Every tool end to end, over MCP, against a real DXF drawing edited in file mode."""
from __future__ import annotations

import ezdxf
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from autocad_mcp import acad, cad
from autocad_mcp.filecad import FileEntity
from autocad_mcp.geometry import point_in_polygon

from conftest import call, call_json

U_SHAPE = [(4, 4), (10, 4), (10, 7), (9, 7), (9, 5), (5, 5), (5, 7), (4, 7)]


def handles_on(layer: str) -> list[str]:
    msp = cad.doc().dxf.modelspace()
    return [e.dxf.handle for e in msp if e.dxf.layer.upper() == layer.upper()]


async def test_status_guides_to_open_a_file_first(backend):
    status = await call_json("autocad_status")
    assert status["mode"].startswith("file")
    assert "open_drawing" in status["next_step"]
    with pytest.raises(ToolError, match="No drawing is open"):
        await call("drawing_summary")


async def test_status_after_opening(opened):
    status = await call_json("autocad_status")
    assert status["active_drawing"]["name"] == "plan.dxf"
    assert status["active_drawing"]["unit_INSUNITS"] == "6 = meter"


async def test_room_areas_ignore_walls_keep_numbers_and_total_right(opened):
    data = await call_json("room_areas")
    assert data["layers_used"] == ["A-AREA"]
    rooms = {r["room"]: r for r in data["rooms"]}
    assert set(rooms) == {"SALA", "COZINHA", "QUARTO", "CIRCULAÇÃO"}
    assert rooms["QUARTO"]["number"] == "101"
    assert rooms["CIRCULAÇÃO"]["area_m2"] == 10
    assert data["total_m2"] == 54
    assert data["total_formatted"] == "54,00 m²"
    assert data["complete"] is True


async def test_room_areas_ask_for_the_layer_when_none_is_known(opened):
    await call("rename_layer", old_name="A-AREA", new_name="ESPACOS")
    with pytest.raises(ToolError, match="Which layer") as err:
        await call("room_areas")
    assert "ESPACOS (4)" in str(err.value)
    data = await call_json("room_areas", layers=["ESPACOS"])
    assert data["total_m2"] == 54


async def test_unreadable_objects_make_the_result_incomplete(opened, monkeypatch):
    original = FileEntity.Area

    def flaky(self):
        if self.e.dxf.handle == handles_on("A-AREA")[0]:
            raise RuntimeError("corrupt outline")
        return original.fget(self)

    monkeypatch.setattr(FileEntity, "Area", property(flaky))
    data = await call_json("room_areas", allow_skips=True)
    assert data["complete"] is False
    assert data["skipped_objects"] == 1
    assert len(data["rooms"]) == 3


async def test_labels_land_inside_u_shaped_rooms_and_are_replaced_not_stacked(opened):
    first = await call("label_room_areas", include_name=True)
    assert "4 area label(s)" in first
    second = await call("label_room_areas", include_name=True)
    assert "replaced" in second
    texts = [e for e in cad.doc().dxf.modelspace() if e.dxftype() == "TEXT" and e.dxf.layer == "A-ANNO-TEXT"
             and e.dxf.text.startswith("A = ")]
    assert len(texts) == 4
    circ = next(t for t in texts if t.dxf.text == "A = 10,00 m²")
    assert point_in_polygon((circ.dxf.align_point.x, circ.dxf.align_point.y), U_SHAPE)


async def test_area_table_is_drawn(opened):
    result = await call("create_area_table", position=[20, 0])
    assert "4 room(s)" in result
    block = next(b for b in cad.doc().dxf.blocks if b.name.startswith("MCP_TABLE_"))
    texts = {e.dxf.text for e in block if e.dxftype() == "TEXT"}
    assert {"AREA SCHEDULE", "TOTAL", "54,00", "CIRCULAÇÃO"} <= texts


async def test_csv_exports_are_excel_safe(opened, tmp_path):
    await call("create_layer", name="X")
    rooms_csv = tmp_path / "areas.csv"
    await call("export_areas_csv", file_path=str(rooms_csv))
    lines = rooms_csv.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Room;Number;Area")
    assert any(line.startswith("QUARTO;101;12,00") for line in lines)
    with pytest.raises(ToolError, match="already exists"):
        await call("export_areas_csv", file_path=str(rooms_csv))


async def test_block_takeoff_groups_by_attribute(opened, tmp_path):
    data = await call_json("count_blocks", group_by_attribute="TIPO")
    counts = {(i["block"], i["TIPO"]): i["count"] for i in data["items"]}
    assert counts == {("JANELA", "J1"): 2, ("PORTA", "P1"): 3}
    out = tmp_path / "q.csv"
    await call("export_quantities_csv", file_path=str(out), group_by_attribute="TIPO")
    assert "PORTA;P1;3" in out.read_text(encoding="utf-8-sig")


async def test_lengths_include_splines_and_hatches(opened):
    data = await call_json("lengths_by_layer")
    by_layer = {r["layer"]: r for r in data["by_layer"]}
    assert by_layer["RODAPE"]["length_m"] > 3
    assert by_layer["A-FLOR-PATT"]["hatch_area_m2"] == 4


async def test_query_finds_every_dimension_kind(opened):
    data = await call_json("query_entities", types=["dimension"])
    kinds = {o["type"] for o in data["objects"]}
    assert kinds == {"AcDbRotatedDimension", "AcDbRadialDimension"}


async def test_walls_are_one_undo_step(opened):
    before = len(cad.doc().dxf.modelspace())
    result = await call("draw_walls", path=[[0, 20], [5, 20], [5, 24]], thickness=0.15)
    assert "2 outline(s)" in result
    assert len(cad.doc().dxf.modelspace()) == before + 2
    await call("undo_last")
    assert len(cad.doc().dxf.modelspace()) == before


async def test_a_write_failing_halfway_is_rolled_back(opened, monkeypatch):
    doors = [h for h in handles_on("A-DOOR")]
    original = FileEntity.Move
    moved = []

    def move_then_fail(self, p1, p2):
        if moved:
            raise RuntimeError("disk full")
        original(self, p1, p2)
        moved.append(self.Handle)

    monkeypatch.setattr(FileEntity, "Move", move_then_fail)
    with pytest.raises(ToolError, match="Nothing was changed"):
        await call("move_entities", handles=doors, offset=[10, 0])
    xs = sorted(cad.doc().HandleToObject(h).InsertionPoint[0] for h in doors)
    assert xs == [1, 3, 6]


async def test_change_properties_snaps_lineweight(opened):
    wall = handles_on("A-WALL")[0]
    await call("change_properties", handles=[wall], lineweight_mm=0.12, color="red")
    ent = cad.doc().HandleToObject(wall)
    assert ent.Lineweight == 13
    assert ent.Color == 1


async def test_edit_tools(opened):
    line = (await call("draw_line", start=[0, 30], end=[2, 30], layer="TMP")).split("handle ")[1].rstrip(").")
    await call("copy_entities", handles=[line], offset=[0, 1], times=3)
    await call("rotate_entities", handles=[line], base=[0, 30], angle_deg=90)
    end = cad.doc().HandleToObject(line).EndPoint
    assert end[0] == pytest.approx(0) and end[1] == pytest.approx(32)
    await call("scale_entities", handles=[line], base=[0, 30], factor=2)
    assert cad.doc().HandleToObject(line).Length == pytest.approx(4)
    await call("mirror_entities", handles=[line], p1=[1, 0], p2=[1, 1])
    offset = await call("offset_entities", handles=[line], distance=0.5)
    assert "1 offset(s)" in offset
    await call("delete_entities", handles=[line])
    with pytest.raises(ToolError):
        await call("get_entities", handles=[line])


async def test_draw_tools(opened):
    for name, args in [
        ("draw_polyline", {"points": [[0, 40], [1, 40], [1, 41]], "closed": True}),
        ("draw_rectangle", {"corner": [0, 50], "width": 2, "height": 1}),
        ("draw_circle", {"center": [0, 60], "radius": 1}),
        ("draw_arc", {"center": [0, 70], "radius": 1, "start_angle_deg": 0, "end_angle_deg": 90}),
        ("draw_text", {"text": "NOTA", "position": [0, 80], "height": 0.2}),
        ("draw_text", {"text": "LONGA", "position": [0, 81], "height": 0.2, "multiline_width": 3}),
        ("add_dimension", {"start": [0, 90], "end": [3, 90], "text_position": [1, 91], "kind": "linear"}),
        ("draw_hatch", {"boundary": [[0, 100], [2, 100], [2, 102], [0, 102]], "pattern": "ANSI31", "scale": 2}),
        ("insert_block", {"name": "PORTA", "position": [0, 110], "attributes": {"TIPO": "P2"}}),
    ]:
        result = await call(name, **args)
        assert "created" in result or "inserted" in result
    data = await call_json("count_blocks", group_by_attribute="TIPO")
    assert any(i["TIPO"] == "P2" for i in data["items"])


async def test_layers(opened):
    layers = await call_json("list_layers")
    by_name = {l["name"]: l for l in layers["layers"]}
    assert by_name["A-AREA"]["objects"] == 4
    await call("create_layer", name="A-FURN", color="gray", lineweight_mm=0.13, make_current=True)
    await call("set_layer_properties", names=["A-FURN"], on=False, locked=True)
    await call("set_current_layer", name="0")
    with pytest.raises(ToolError, match="not found"):
        await call("merge_layers", sources=["A-FURN", "NOPE"], target="A-WALL")
    assert "A-FURN" in {l.dxf.name for l in cad.doc().dxf.layers}
    merged = await call("merge_layers", sources=["Walls_old"], target="A-WALL")
    assert "1 object(s) moved, layer deleted" in merged


async def test_layer_standard(opened):
    plan = await call_json("check_layer_standard")
    assert ["Walls_old", "A-WALL"] in plan["merge"]
    result = await call("apply_layer_standard")
    assert "merged Walls_old -> A-WALL" in result


async def test_save_open_and_read_only(opened, tmp_path, monkeypatch):
    target = tmp_path / "copy.dxf"
    await call("save_drawing", file_path=str(target))
    assert ezdxf.readfile(target).modelspace()
    with pytest.raises(ToolError, match="already exists"):
        await call("save_drawing", file_path=str(target))
    monkeypatch.setattr(acad, "READ_ONLY", True)
    with pytest.raises(ToolError, match="READ-ONLY"):
        await call("draw_line", start=[0, 0], end=[1, 1])
    with pytest.raises(ToolError, match="READ-ONLY"):
        await call("open_drawing", file_path=str(target), read_only=False)
    assert "read-only" in await call("open_drawing", file_path=str(target))


async def test_documents_and_variables(opened):
    await call("new_drawing")
    docs = await call_json("list_documents")
    assert [d["active"] for d in docs] == [False, True]
    await call("switch_document", name="plan")
    variables = await call_json("get_variables", names=["INSUNITS", "NOPE"])
    assert variables == {"INSUNITS": 6, "NOPE": "(no such variable)"}
    assert "LTSCALE" in await call("set_variable", name="LTSCALE", value="2,5")
    with pytest.raises(ToolError, match="allowed list"):
        await call("set_variable", name="SECURELOAD", value="0")


async def test_summary_blocks_layouts_purge_audit(opened):
    summary = await call_json("drawing_summary")
    assert summary["most_used_blocks"] == {"PORTA": 3, "JANELA": 2}
    blocks = await call_json("list_blocks")
    assert {b["block"]: b["attributes"] for b in blocks["blocks"]} == {"PORTA": ["TIPO"], "JANELA": ["TIPO"]}
    assert "skipped_objects" not in blocks
    assert await call_json("list_layouts")
    await call("create_layer", name="UNUSED")
    assert "layers" in await call("purge_drawing")
    assert "Audit done" in await call("audit_drawing")
    with pytest.raises(ToolError, match="needs AutoCAD"):
        await call("export_layouts_pdf")


async def test_network_paths_are_refused(opened):
    with pytest.raises(ToolError, match="Network paths"):
        await call("export_areas_csv", file_path="\\\\attacker\\share\\x.csv")
    with pytest.raises(ToolError, match="Network paths"):
        await call("open_drawing", file_path="\\\\attacker\\share\\x.dwg")
