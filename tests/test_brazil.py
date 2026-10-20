"""Brazilian office work: door and window schedule, site areas with TO/CA/TP, pt-BR and en-US labels, batch PDF."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from autocad_mcp import cad
from autocad_mcp.openings import opening_kind, to_meters
from autocad_mcp.openings import _sizes as sizes
from autocad_mcp.tools.output import export_document, export_many

from conftest import call, call_json


def test_sizes_in_any_unit_and_format():
    assert [to_meters(v) for v in ("0,80", "0.8", "80", "800", "2,10 m", "")] == [0.8, 0.8, 0.8, 0.8, 2.1, None]
    assert sizes({"LARGURA": "90", "ALTURA": "2,10", "PEITORIL": "1,10"}) == (0.9, 2.1, 1.1)
    assert sizes({"DIM": "120x100"}) == (1.2, 1.0, None)
    assert opening_kind("P1", "0") == "door"
    assert opening_kind("BLOCO", "ARQ-CXO") == "window"
    assert opening_kind("CADEIRA", "ARQ-MOB") == "opening"


async def test_openings_schedule_groups_by_code_and_measures_widths(opened):
    data = await call_json("takeoff", kind="openings")
    rows = {i["code"]: i for i in data["items"]}
    assert [i["code"] for i in data["items"]] == ["P1", "J1"]
    assert (rows["P1"]["kind"], rows["P1"]["count"], rows["P1"]["width_m"]) == ("door", 3, 0.8)
    assert (rows["J1"]["kind"], rows["J1"]["count"], rows["J1"]["width_m"]) == ("window", 2, 1.2)
    assert rows["P1"]["width_from"] == "measured"
    assert "ALTURA" in data["note"]
    assert "2 row(s)" in await call("place_table", report="openings", position=[40, 20])
    texts = {e.dxf.text for b in cad.doc().dxf.blocks if b.name.startswith("MCP_TABLE_") for e in b
             if e.dxftype() == "TEXT"}
    assert {"QUADRO DE ESQUADRIAS", "PORTA", "JANELA", "0,80", "1,20"} <= texts


async def test_openings_read_sizes_from_attributes(opened):
    dxf = cad.doc().dxf
    block = dxf.blocks.new("PORTA_GIRO")
    block.add_line((0, 0), (0.9, 0))
    for tag in ("TIPO", "LARGURA", "ALTURA"):
        block.add_attdef(tag, (0, 0), dxfattribs={"height": 0.1})
    for x in (30, 32):
        dxf.modelspace().add_blockref("PORTA_GIRO", (x, 0), dxfattribs={"layer": "ARQ-POR"}).add_auto_attribs(
            {"TIPO": "P2", "LARGURA": "90", "ALTURA": "210"})
    data = await call_json("takeoff", kind="openings", names=["PORTA_GIRO"])
    item = data["items"][0]
    assert (item["code"], item["count"], item["width_m"], item["height_m"]) == ("P2", 2, 0.9, 2.1)
    assert item["area_each_m2"] == 1.89
    assert item["area_total_m2"] == 3.78


async def draw_site() -> None:
    await call("draw", items=[
        {"shape": "rectangle", "corner": [-50, -50], "width": 20, "height": 30, "layer": "TERRENO"},
        {"shape": "rectangle", "corner": [0, 100], "width": 10, "height": 8, "layer": "AREA CONSTRUIDA"},
        {"shape": "rectangle", "corner": [100, 100], "width": 10, "height": 8, "layer": "AREA CONSTRUIDA"},
        {"shape": "rectangle", "corner": [-50, -50], "width": 6, "height": 10, "layer": "PERMEAVEL"},
    ])


async def test_site_areas_and_zoning_checks(opened):
    await draw_site()
    floors = {"TÉRREO": [[-1, 99], [12, 110]], "SUPERIOR": [[99, 99], [112, 110]]}
    data = await call_json("site_areas", floors=floors, limits={"TO": 50, "CA": 0.2, "TP": 15})
    assert data["lot_m2"] == 600
    assert data["floors"] == [{"floor": "TÉRREO", "built_m2": 80}, {"floor": "SUPERIOR", "built_m2": 80}]
    assert data["built_total_m2"] == 160
    assert data["projection_m2"] == 80
    assert "projection_note" in data
    assert data["indices"] == {"TO": 0.1333, "CA": 0.2667, "TP": 0.1}
    assert {k: v["ok"] for k, v in data["checks"].items()} == {"TO": True, "CA": False, "TP": False}


async def test_site_schedule_csv_and_missing_layers(opened, tmp_path):
    with pytest.raises(ToolError, match="Which layer holds the lot and the built outline"):
        await call("site_areas")
    await draw_site()
    target = tmp_path / "site.csv"
    await call("export_csv", report="site", file_path=str(target), limits={"CA": 0.2})
    lines = target.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "DESCRIÇÃO;VALOR;UNIDADE;LIMITE;SITUAÇÃO"
    assert "ÁREA DO TERRENO;600,00;m²;;" in lines
    assert "COEFICIENTE DE APROVEITAMENTO (CA);0,27;;0,20;ACIMA DO LIMITE" in lines
    assert "TAXA DE PERMEABILIDADE (TP);10,00;%;;" in lines


async def test_english_labels(opened, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCAD_MCP_LOCALE", "en-US")
    target = tmp_path / "rooms.csv"
    await call("export_csv", report="rooms", file_path=str(target))
    lines = target.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "ROOM,No.,AREA (m²),PERIMETER (m)"
    assert lines[-1] == "TOTAL,,54.00,"


class FakeLayout:
    def __init__(self, name: str, order: int) -> None:
        self.Name, self.TabOrder, self.ConfigName = name, order, "DWG To PDF.pc3"


class FakeDoc:
    def __init__(self, path: str, app: "FakeApp") -> None:
        self.FullName, self.Name, self.app = path, os.path.basename(path), app
        self.Layouts = [FakeLayout("Model", 0), FakeLayout("A1", 1), FakeLayout("A2", 2)]
        self.ActiveLayout = self.Layouts[0]
        self.Plot = SimpleNamespace(PlotToFile=self.plot)
        self.closed = False

    def plot(self, path: str, *_: Any) -> bool:
        Path(path).write_bytes(b"%PDF")
        return True

    def GetVariable(self, _: str) -> int:
        return 1

    def SetVariable(self, *_: Any) -> None:
        pass

    def Regen(self, _: int) -> None:
        pass

    def Activate(self) -> None:
        self.app.ActiveDocument = self

    def Close(self, save: bool) -> None:
        assert save is False
        self.closed = True
        self.app.Documents.remove(self)


class FakeApp:
    def __init__(self) -> None:
        self.Documents = FakeDocuments(self)
        self.ActiveDocument = None


class FakeDocuments(list):
    def __init__(self, app: FakeApp) -> None:
        super().__init__()
        self.app = app
        self.opened: list[tuple[str, bool]] = []

    def Open(self, path: str, read_only: bool) -> FakeDoc:
        self.opened.append((path, read_only))
        doc = FakeDoc(path, self.app)
        self.append(doc)
        return doc


def test_batch_pdf_opens_read_only_closes_and_restores(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    for name in ("a.dwg", "b.dwg", "notes.txt"):
        (folder / name).write_bytes(b"x")
    app = FakeApp()
    already_open = FakeDoc(str(folder / "a.dwg"), app)
    app.Documents.append(already_open)
    app.ActiveDocument = already_open
    result = export_many(app, [str(folder)], str(tmp_path / "out"), lambda d, f: export_document(d, None, f, False, ""))
    assert result["files_created"] == 4
    assert result["failures"] == 0
    assert app.Documents.opened == [(str(folder / "b.dwg"), True)]
    assert app.Documents == [already_open]
    assert app.ActiveDocument is already_open
    assert sorted(os.listdir(tmp_path / "out")) == ["a - A1.pdf", "a - A2.pdf", "b - A1.pdf", "b - A2.pdf"]
    again = export_many(app, [str(folder / "a.dwg")], str(tmp_path / "out"),
                        lambda d, f: export_document(d, ["A9"], f, False, ""))
    assert "not found" in again["drawings"][0]["failed"][0]
