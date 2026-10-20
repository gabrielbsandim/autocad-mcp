"""The CAD thread: busy retries for reads only, rollback of half-done writes, timeouts."""
from __future__ import annotations

import asyncio
import sys
import threading
from typing import Any

import pytest

from autocad_mcp import acad, cad
from autocad_mcp.errors import AcadError


class Busy(Exception):
    pass


class FakeDoc:
    Name = "fake.dwg"

    def __init__(self) -> None:
        self.marks: list[str] = []

    def StartUndoMark(self) -> None:
        self.marks.append("start")

    def EndUndoMark(self) -> None:
        self.marks.append("end")


class FakeApp:
    def __init__(self, doc: FakeDoc) -> None:
        self.ActiveDocument = doc
        self.Documents = type("Docs", (), {"Count": 1})()


class FakeBackend:
    name = "fake"
    label = "FakeCAD"

    def __init__(self) -> None:
        self.doc = FakeDoc()
        self.undone = 0
        self.idle = True

    def init_thread(self) -> None:
        return None

    def app(self) -> Any:
        return FakeApp(self.doc)

    def point(self, x: float, y: float, z: float = 0.0) -> Any:
        return (x, y, z)

    def flat(self, values: Any) -> Any:
        return tuple(values)

    def objects(self, objs: Any) -> Any:
        return list(objs)

    def translate(self, exc: BaseException) -> AcadError | None:
        return AcadError("FakeCAD is busy") if isinstance(exc, Busy) else None

    def is_busy(self, exc: BaseException) -> bool:
        return isinstance(exc, Busy)

    def is_fatal(self, exc: BaseException) -> bool:
        return isinstance(exc, Busy)

    def quiescent(self) -> bool:
        return self.idle

    def undo(self, document: Any, steps: int) -> None:
        self.undone += steps


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(acad, "LOG_DIR", tmp_path)
    monkeypatch.setattr(acad, "LOG_FILE", tmp_path / "actions.log")
    monkeypatch.setattr(acad, "READ_ONLY", False)
    monkeypatch.setattr(cad, "BUSY_DELAY", 0)
    b = FakeBackend()
    cad.use(b)
    yield b
    cad.use(None)


async def test_reads_retry_while_busy(fake):
    calls = []

    def read() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise Busy()
        return "ok"

    assert await cad.run(read) == "ok"
    assert len(calls) == 3


async def test_a_write_that_hits_busy_runs_once_and_is_rolled_back(fake):
    calls = []

    def body(d: Any) -> str:
        calls.append(1)
        raise Busy()

    with pytest.raises(AcadError, match="Nothing was changed"):
        await acad.change("draw_line", {}, body)
    assert len(calls) == 1
    assert fake.undone == 1
    assert fake.doc.marks == ["start", "end"]
    assert '"ok": false' in acad.LOG_FILE.read_text()


async def test_writes_wait_for_an_idle_cad(fake, monkeypatch):
    monkeypatch.setattr(cad, "READY_WAIT", 0.3)
    fake.idle = False
    with pytest.raises(AcadError, match="busy"):
        await acad.change("draw_line", {}, lambda d: "drawn")
    assert fake.doc.marks == []


async def test_read_only_mode_blocks_writes_and_file_actions(fake, monkeypatch):
    monkeypatch.setattr(acad, "READ_ONLY", True)
    with pytest.raises(AcadError, match="READ-ONLY"):
        await acad.change("draw_line", {}, lambda d: "drawn")
    with pytest.raises(AcadError, match="READ-ONLY"):
        await acad.act("export_areas_csv", {}, lambda: "saved")
    assert await acad.act("open_drawing", {}, lambda: "opened", writes=False) == "opened"


async def test_a_hung_cad_times_out_and_later_calls_fail_fast(fake):
    release = threading.Event()

    def hang() -> str:
        release.wait(5)
        return "late"

    with pytest.raises(AcadError, match="did not answer"):
        await cad.run(hang, limit=0.2)
    await asyncio.sleep(0.3)
    with pytest.raises(AcadError, match="has not answered"):
        await cad.run(lambda: "next")
    release.set()
    await asyncio.sleep(0.1)
    assert await cad.run(lambda: "next") == "next"


@pytest.mark.skipif(sys.platform != "win32", reason="pywin32 exists only on Windows")
def test_com_errors_are_translated():
    import pywintypes

    from autocad_mcp.com import RPC_E_CALL_REJECTED, ComBackend

    b = ComBackend()
    busy = pywintypes.com_error(RPC_E_CALL_REJECTED, "rejected", None, None)
    assert b.is_busy(busy)
    assert "busy" in str(b.translate(busy))
    assert b.translate(ValueError("x")) is None


def test_other_cads_object_names_are_normalized():
    ent = type("E", (), {"ObjectName": "ZcDbLine"})()
    assert acad.kind(ent) == "AcDbLine"
    ent.ObjectName = "AcDbCircle"
    assert acad.kind(ent) == "AcDbCircle"


def test_lineweights_snap_to_valid_values():
    assert acad.lineweight_enum(0.12) == 13
    assert acad.lineweight_enum(0.25) == 25
    assert acad.lineweight_enum(-1) == acad.LW_BYLAYER


def test_dimension_alias_covers_every_dimension_type():
    types = acad.normalize_types(["dimension"])
    assert {"AcDbRadialDimension", "AcDbAngularDimension", "AcDbOrdinateDimension"} <= types
