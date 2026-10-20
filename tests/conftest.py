from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ezdxf
import pytest

from autocad_mcp import acad, cad
from autocad_mcp.filecad import FileBackend
from autocad_mcp.server import mcp


def build_plan(path: Path) -> Path:
    """A small floor plan in meters: rooms on A-AREA, walls that are also closed outlines,
    doors and windows with a TIPO attribute, a spline baseboard and a hatch."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    for name in ("A-AREA", "A-WALL", "A-ANNO-TEXT", "A-DOOR", "A-GLAZ", "RODAPE", "A-FLOR-PATT", "Walls_old"):
        doc.layers.add(name)
    msp = doc.modelspace()
    rooms = {
        "SALA": [(0, 0), (5, 0), (5, 4), (0, 4)],
        "COZINHA": [(5, 0), (8, 0), (8, 4), (5, 4)],
        "QUARTO": [(0, 4), (4, 4), (4, 7), (0, 7)],
        "CIRCULAÇÃO": [(4, 4), (10, 4), (10, 7), (9, 7), (9, 5), (5, 5), (5, 7), (4, 7)],
    }
    for name, pts in rooms.items():
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_text("SALA", dxfattribs={"insert": (2, 2), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_text("12,00 m²", dxfattribs={"insert": (6, 1), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_text("COZINHA", dxfattribs={"insert": (6, 2), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_text("101", dxfattribs={"insert": (1, 6), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_text("QUARTO", dxfattribs={"insert": (1, 5), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_text("CIRCULAÇÃO", dxfattribs={"insert": (9.5, 6), "height": 0.2, "layer": "A-ANNO-TEXT"})
    msp.add_lwpolyline([(-0.15, -0.15), (8.15, -0.15), (8.15, 0), (-0.15, 0)], close=True,
                       dxfattribs={"layer": "A-WALL"})
    msp.add_line((0, 10), (3, 10), dxfattribs={"layer": "Walls_old"})

    door = doc.blocks.new("PORTA")
    door.add_line((0, 0), (0.8, 0))
    door.add_arc((0, 0), 0.8, 0, 90)
    door.add_attdef("TIPO", (0, 0), dxfattribs={"height": 0.1})
    window = doc.blocks.new("JANELA")
    window.add_lwpolyline([(0, 0), (1.2, 0), (1.2, 0.15), (0, 0.15)], close=True)
    window.add_attdef("TIPO", (0, 0), dxfattribs={"height": 0.1})
    for x in (1, 3, 6):
        msp.add_blockref("PORTA", (x, 0), dxfattribs={"layer": "A-DOOR"}).add_auto_attribs({"TIPO": "P1"})
    for x in (2, 7):
        msp.add_blockref("JANELA", (x, 4), dxfattribs={"layer": "A-GLAZ"}).add_auto_attribs({"TIPO": "J1"})

    msp.add_spline([(0, 0), (1, 1), (2, 0), (3, 1)], dxfattribs={"layer": "RODAPE"})
    hatch = msp.add_hatch(dxfattribs={"layer": "A-FLOR-PATT"})
    hatch.paths.add_polyline_path([(0, 0), (2, 0), (2, 2), (0, 2)], is_closed=True)
    msp.add_aligned_dim(p1=(0, 0), p2=(5, 0), distance=-1).render()
    msp.add_radius_dim(center=(20, 20), radius=2, angle=45).render()
    msp.add_circle((20, 20), 2)
    doc.saveas(path)
    return path


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FileBackend:
    monkeypatch.setattr(acad, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(acad, "LOG_FILE", tmp_path / "log" / "actions.log")
    monkeypatch.setattr(acad, "READ_ONLY", False)
    monkeypatch.setenv("AUTOCAD_MCP_LOCALE", "pt-BR")
    b = FileBackend()
    cad.use(b)
    yield b
    cad.use(None)


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    return build_plan(tmp_path / "plan.dxf")


async def call(tool: str, /, **args: Any) -> str:
    result = await mcp.call_tool(tool, args)
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def call_json(tool: str, /, allow_skips: bool = False, **args: Any) -> Any:
    data = json.loads(await call(tool, **args))
    if isinstance(data, dict) and not allow_skips:
        assert "skipped_objects" not in data, data.get("skipped_reasons")
    return data


@pytest.fixture
async def opened(backend: FileBackend, plan: Path) -> FileBackend:
    await call("open_drawing", file_path=str(plan), read_only=False)
    return backend
