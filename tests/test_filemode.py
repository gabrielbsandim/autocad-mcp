"""File mode as a Mac or AutoCAD LT user gets it: sheets drawn to SVG or PDF without a CAD, one by one or in batch."""
from __future__ import annotations

import shutil

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from autocad_mcp import cad

from conftest import call, call_json


async def test_status_reports_dwg_support(opened):
    status = await call_json("status")
    assert status["dwg_support"].startswith(("yes", "no"))


async def test_svg_of_model_space_when_no_sheet_has_content(opened, tmp_path):
    result = await call_json("export_pdf", format="svg", output_folder=str(tmp_path))
    assert [p.split("/")[-1].split("\\")[-1] for p in result["created"]] == ["plan - Model.svg"]
    assert "<svg" in (tmp_path / "plan - Model.svg").read_text(encoding="utf-8")
    again = await call_json("export_pdf", format="svg", output_folder=str(tmp_path))
    assert "already exists" in again["failed"][0]
    with pytest.raises(ToolError, match="Sheet\\(s\\) not found"):
        await call("export_pdf", format="svg", layouts=["Nope"], output_folder=str(tmp_path))


async def test_pdf_of_a_sheet(opened, tmp_path):
    pytest.importorskip("matplotlib")
    dxf = cad.doc().dxf
    dxf.layouts.get("Layout1").add_line((0, 0), (100, 50))
    result = await call_json("export_pdf", output_folder=str(tmp_path))
    assert len(result["created"]) == 1
    assert (tmp_path / "plan - Layout1.pdf").read_bytes().startswith(b"%PDF")


async def test_batch_export_of_a_folder_in_file_mode(opened, plan, tmp_path):
    folder = tmp_path / "projeto"
    folder.mkdir()
    shutil.copy(plan, folder / "terreo.dxf")
    shutil.copy(plan, folder / "superior.dxf")
    result = await call_json("export_pdf", format="svg", drawings=[str(folder)], output_folder=str(tmp_path / "out"))
    assert result["files_created"] == 2
    assert result["failures"] == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["superior - Model.svg", "terreo - Model.svg"]
    status = await call_json("status")
    assert [d["name"] for d in status["open_drawings"]] == ["plan.dxf"]
    assert status["active_drawing"]["name"] == "plan.dxf"
