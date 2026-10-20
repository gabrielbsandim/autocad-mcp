"""Every tool end to end, over MCP, against a real DXF drawing edited in file mode."""
from __future__ import annotations

import ezdxf
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from autocad_mcp import acad, cad
from autocad_mcp.filecad import FileEntity
from autocad_mcp.geometry import point_in_polygon
from autocad_mcp.server import mcp

from conftest import call, call_json

U_SHAPE = [(4, 4), (10, 4), (10, 7), (9, 7), (9, 5), (5, 5), (5, 7), (4, 7)]


def handles_on(layer: str) -> list[str]:
    msp = cad.doc().dxf.modelspace()
    return [e.dxf.handle for e in msp if e.dxf.layer.upper() == layer.upper()]


async def test_every_tool_has_a_title_and_a_safety_hint():
    tools = await mcp.list_tools()
    assert len(tools) <= 25
    for t in tools:
        assert t.title, t.name
        assert t.annotations.read_only_hint or t.annotations.destructive_hint is not None, t.name
        assert t.annotations.open_world_hint is False


async def test_status_guides_to_open_a_file_first(backend):
    status = await call_json("status")
    assert status["mode"].startswith("file")
    assert "open_drawing" in status["next_step"]
    with pytest.raises(ToolError, match="No drawing is open"):
        await call("describe_drawing")


async def test_status_after_opening(opened):
    status = await call_json("status")
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
    await call("manage_layer", action="rename", names=["A-AREA"], new_name="ESPACOS")
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
    first = await call("label_rooms", include_name=True)
    assert "4 area label(s)" in first
    second = await call("label_rooms", include_name=True)
    assert "replaced" in second
    texts = [e for e in cad.doc().dxf.modelspace() if e.dxftype() == "TEXT" and e.dxf.layer == "A-ANNO-TEXT"
             and e.dxf.text.startswith("A = ")]
    assert len(texts) == 4
    circ = next(t for t in texts if t.dxf.text == "A = 10,00 m²")
    assert point_in_polygon((circ.dxf.align_point.x, circ.dxf.align_point.y), U_SHAPE)


async def test_tables_are_drawn_for_every_report(opened):
    assert "4 row(s)" in await call("place_table", report="rooms", position=[20, 0])
    assert "2 row(s)" in await call("place_table", report="blocks", position=[40, 0], group_by_attribute="TIPO")
    assert "row(s)" in await call("place_table", report="lengths", position=[60, 0])
    blocks = [b for b in cad.doc().dxf.blocks if b.name.startswith("MCP_TABLE_")]
    texts = {e.dxf.text for b in blocks for e in b if e.dxftype() == "TEXT"}
    assert {"QUADRO DE ÁREAS", "TOTAL", "54,00", "CIRCULAÇÃO", "P1", "QUANTITATIVO DE BLOCOS"} <= texts


async def test_csv_exports_are_excel_safe(opened, tmp_path):
    rooms_csv = tmp_path / "areas.csv"
    await call("export_csv", report="rooms", file_path=str(rooms_csv))
    lines = rooms_csv.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "AMBIENTE;Nº;ÁREA (m²);PERÍMETRO (m)"
    assert "QUARTO;101;12,00;14,00" in lines
    assert lines[-1] == "TOTAL;;54,00;"
    with pytest.raises(ToolError, match="already exists"):
        await call("export_csv", report="rooms", file_path=str(rooms_csv))
    blocks_csv = tmp_path / "q.csv"
    await call("export_csv", report="blocks", file_path=str(blocks_csv), group_by_attribute="TIPO")
    assert "PORTA;P1;3" in blocks_csv.read_text(encoding="utf-8-sig")


async def test_block_takeoff_groups_by_attribute(opened):
    data = await call_json("takeoff", group_by_attribute="TIPO")
    counts = {(i["block"], i["TIPO"]): i["count"] for i in data["items"]}
    assert counts == {("JANELA", "J1"): 2, ("PORTA", "P1"): 3}
    only_doors = await call_json("takeoff", names=["PORT*"])
    assert only_doors["total_blocks"] == 3


async def test_lengths_include_splines_and_hatches(opened):
    data = await call_json("takeoff", kind="lengths")
    by_layer = {r["layer"]: r for r in data["by_layer"]}
    assert by_layer["RODAPE"]["length_m"] > 3
    assert by_layer["A-FLOR-PATT"]["hatch_area_m2"] == 4


async def test_find_entities_filters_and_handles(opened):
    dims = await call_json("find_entities", types=["dimension"])
    assert {o["type"] for o in dims["objects"]} == {"AcDbRotatedDimension", "AcDbRadialDimension"}
    texts = await call_json("find_entities", text_contains="quart")
    assert [o["text"] for o in texts["objects"]] == ["QUARTO"]
    doors = await call_json("find_entities", block_name="PORTA", region=[[0, -1], [4, 1]])
    assert doors["found"] == 2
    detail = await call_json("find_entities", handles=[doors["objects"][0]["handle"]])
    assert detail["objects"][0]["attributes"] == {"TIPO": "P1"}
    with pytest.raises(ToolError, match="Nothing is selected"):
        await call("find_entities", handles=["selection"])


async def test_draw_many_items_is_one_undo_step(opened):
    before = len(cad.doc().dxf.modelspace())
    result = await call("draw", layer="NEW", items=[
        {"shape": "line", "start": [0, 30], "end": [2, 30]},
        {"shape": "polyline", "points": [[0, 40], [1, 40], [1, 41]], "closed": True},
        {"shape": "rectangle", "corner": [0, 50], "width": 2, "height": 1},
        {"shape": "circle", "center": [0, 60], "radius": 1},
        {"shape": "arc", "center": [0, 70], "radius": 1, "start_angle_deg": 0, "end_angle_deg": 90},
        {"shape": "walls", "path": [[0, 20], [5, 20], [5, 24]], "thickness": 0.15, "layer": "A-WALL"},
        {"shape": "text", "text": "NOTA", "position": [0, 80], "height": 0.2},
        {"shape": "text", "text": "LONGA", "position": [0, 81], "height": 0.2, "multiline_width": 3},
        {"shape": "dimension", "start": [0, 90], "end": [3, 90], "text_position": [1, 91], "kind": "linear"},
        {"shape": "hatch", "boundary": [[0, 100], [2, 100], [2, 102], [0, 102]], "pattern": "ANSI31", "scale": 2},
        {"shape": "block", "name": "PORTA", "position": [0, 110], "attributes": {"TIPO": "P2"}},
    ])
    assert result.startswith("12 object(s) created")
    msp = cad.doc().dxf.modelspace()
    assert len(msp) == before + 12
    assert sum(1 for e in msp if e.dxf.layer == "A-WALL") == 3
    await call("undo")
    assert len(cad.doc().dxf.modelspace()) == before


async def test_draw_validates_items(opened):
    with pytest.raises(ToolError):
        await call("draw", items=[{"shape": "circle", "center": [0, 0], "radius": -1}])
    with pytest.raises(ToolError):
        await call("draw", items=[{"shape": "spiral", "center": [0, 0]}])


async def test_a_write_failing_halfway_is_rolled_back(opened, monkeypatch):
    doors = handles_on("A-DOOR")
    original = FileEntity.Move
    moved = []

    def move_then_fail(self, p1, p2):
        if moved:
            raise RuntimeError("disk full")
        original(self, p1, p2)
        moved.append(self.Handle)

    monkeypatch.setattr(FileEntity, "Move", move_then_fail)
    with pytest.raises(ToolError, match="Nothing was changed"):
        await call("transform", handles=doors, operation="move", offset=[10, 0])
    xs = sorted(cad.doc().HandleToObject(h).InsertionPoint[0] for h in doors)
    assert xs == [1, 3, 6]


async def test_transform_operations(opened):
    created = await call("draw", items=[{"shape": "line", "start": [0, 30], "end": [2, 30]}])
    line = created.split("line ")[1]
    await call("transform", handles=[line], operation="copy", offset=[0, 1], times=3)
    await call("transform", handles=[line], operation="rotate", base=[0, 30], angle_deg=90)
    end = cad.doc().HandleToObject(line).EndPoint
    assert end[0] == pytest.approx(0) and end[1] == pytest.approx(32)
    await call("transform", handles=[line], operation="scale", base=[0, 30], factor=2)
    assert cad.doc().HandleToObject(line).Length == pytest.approx(4)
    assert "1 object(s) mirrored" in await call("transform", handles=[line], operation="mirror", p1=[1, 0], p2=[1, 1])
    assert "1 offset(s)" in await call("transform", handles=[line], operation="offset", distance=0.5)
    with pytest.raises(ToolError, match="`base` is required"):
        await call("transform", handles=[line], operation="rotate", angle_deg=10)
    await call("delete_entities", handles=[line])
    with pytest.raises(ToolError, match="No object with handle"):
        await call("find_entities", handles=[line])


async def test_change_properties_snaps_lineweight(opened):
    wall = handles_on("A-WALL")[0]
    await call("change_properties", handles=[wall], lineweight_mm=0.12, color="red", layer="A-WALL-NEW")
    ent = cad.doc().HandleToObject(wall)
    assert (ent.Lineweight, ent.Color, ent.Layer) == (13, 1, "A-WALL-NEW")


async def test_layers(opened):
    described = await call_json("describe_drawing", sections=["layers"])
    by_name = {l["name"]: l for l in described["layers"]}
    assert by_name["A-AREA"]["objects"] == 4
    await call("manage_layer", action="create", names=["A-FURN"], color="gray", lineweight_mm=0.13)
    await call("manage_layer", action="update", names=["A-FURN"], on=False, locked=True)
    await call("manage_layer", action="set_current", names=["A-FURN"])
    assert cad.doc().GetVariable("CLAYER") == "A-FURN"
    with pytest.raises(ToolError, match="current layer cannot be frozen"):
        await call("manage_layer", action="update", names=["A-FURN"], frozen=True)
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


async def test_asbea_standard_converts_aia_layers_and_rooms_still_add_up(opened):
    plan = await call_json("check_layer_standard", standard="asbea")
    assert ["A-WALL", "ARQ-ALV"] in plan["rename"]
    assert ["A-AREA", "ARQ-___-ARE"] in plan["rename"]
    assert ["Walls_old", "ARQ-ALV"] in plan["merge"]
    await call("apply_layer_standard", standard="asbea")
    data = await call_json("room_areas")
    assert data["layers_used"] == ["ARQ-___-ARE"]
    assert data["total_m2"] == 54
    with pytest.raises(ToolError, match="Network paths"):
        await call("check_layer_standard", standard="\\\\attacker\\share\\std.json")


async def test_save_open_and_read_only(opened, tmp_path, monkeypatch):
    target = tmp_path / "copy.dxf"
    await call("save_drawing", file_path=str(target))
    assert ezdxf.readfile(target).modelspace()
    with pytest.raises(ToolError, match="already exists"):
        await call("save_drawing", file_path=str(target))
    monkeypatch.setattr(acad, "READ_ONLY", True)
    with pytest.raises(ToolError, match="READ-ONLY"):
        await call("draw", items=[{"shape": "line", "start": [0, 0], "end": [1, 1]}])
    with pytest.raises(ToolError, match="READ-ONLY"):
        await call("open_drawing", file_path=str(target), read_only=False)
    with pytest.raises(ToolError, match="READ-ONLY"):
        await call("open_drawing", action="new")
    assert "read-only" in await call("open_drawing", file_path=str(target))


async def test_documents_and_variables(opened):
    await call("open_drawing", action="new")
    status = await call_json("status")
    assert len(status["open_drawings"]) == 2
    assert status["active_drawing"]["name"] != "plan.dxf"
    await call("open_drawing", action="switch", name="plan")
    described = await call_json("describe_drawing", sections=["variables"], variables=["INSUNITS", "NOPE"])
    assert described["variables"] == {"INSUNITS": 6, "NOPE": "(no such variable)"}
    assert "LTSCALE" in await call("set_variable", name="LTSCALE", value="2,5")
    with pytest.raises(ToolError, match="allowed list"):
        await call("set_variable", name="SECURELOAD", value="0")


async def test_describe_purge_audit(opened):
    described = await call_json("describe_drawing", sections=["summary", "blocks", "layouts"])
    assert described["summary"]["most_used_blocks"] == {"PORTA": 3, "JANELA": 2}
    assert {b["block"]: b["attributes"] for b in described["blocks"]} == {"PORTA": ["TIPO"], "JANELA": ["TIPO"]}
    assert described["layouts"][0]["name"] == "Layout1"
    await call("manage_layer", action="create", names=["UNUSED"])
    assert "layers" in await call("maintain_drawing", action="purge")
    assert "Audit done" in await call("maintain_drawing", action="audit")


async def test_network_paths_are_refused(opened):
    with pytest.raises(ToolError, match="Network paths"):
        await call("export_csv", report="rooms", file_path="\\\\attacker\\share\\x.csv")
    with pytest.raises(ToolError, match="Network paths"):
        await call("open_drawing", file_path="\\\\attacker\\share\\x.dwg")
    with pytest.raises(ToolError, match="Network paths"):
        await call("draw", items=[{"shape": "block", "name": "\\\\attacker\\share\\x.dwg", "position": [0, 0]}])
