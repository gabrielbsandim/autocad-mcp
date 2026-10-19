"""Helpers on top of the AutoCAD object model (they run on the COM thread)."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pythoncom
import win32com.client

from autocad_mcp import com
from autocad_mcp.com import AcadError
from autocad_mcp.geometry import flat_to_points

READ_ONLY = os.environ.get("AUTOCAD_MCP_READONLY", "").strip().lower() in ("1", "true", "yes")

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "autocad-mcp"
LOG_FILE = LOG_DIR / "actions.log"

# Maximum number of entities scanned per query, so huge drawings don't hang the server.
MAX_SCAN = int(os.environ.get("AUTOCAD_MCP_MAX_SCAN", "200000"))


# ---------------------------------------------------------------- variants

def vpoint(x: float, y: float, z: float = 0.0) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z)))


def vflat(values: Iterable[float]) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(float(v) for v in values))


def vobjects(objs: Sequence[Any]) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(objs))


def _pt(p: Sequence[float]) -> list[float]:
    return [round(float(v), 4) for v in p]


# ---------------------------------------------------------------- log / writes

def log_action(tool: str, args: dict[str, Any], result: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            name = com.doc().Name
        except Exception:  # noqa: BLE001
            name = "?"
        line = {
            "when": datetime.now().isoformat(timespec="seconds"),
            "drawing": name,
            "tool": tool,
            "args": args,
            "result": result[:500],
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break an action
        pass


def ensure_writable() -> None:
    if READ_ONLY:
        raise AcadError(
            "The server is in READ-ONLY mode (AUTOCAD_MCP_READONLY=1). "
            "Nothing was changed. To allow changes, remove that option from the Claude Desktop configuration."
        )


@contextmanager
def undo_group(document: Any):
    """Group everything done here into a single undo step (one Ctrl+Z / U undoes it all)."""
    document.StartUndoMark()
    try:
        yield
    finally:
        try:
            document.EndUndoMark()
        except Exception:  # noqa: BLE001
            pass


def write(tool: str, args: dict[str, Any], fn: Callable[[Any], str]) -> Callable[[], str]:
    """Wrap a write operation: check read-only mode, group the undo, write to the log."""

    def runner() -> str:
        ensure_writable()
        document = com.doc()
        with undo_group(document):
            result = fn(document)
        log_action(tool, args, result)
        return result

    return runner


# ---------------------------------------------------------------- entities

def space(document: Any, where: str = "model") -> Any:
    """'model' = model space; 'paper' = active layout."""
    return document.PaperSpace if where == "paper" else document.ModelSpace


def by_handle(document: Any, handle: str) -> Any:
    try:
        return document.HandleToObject(str(handle).strip())
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"No object with handle {handle!r} in the active drawing.") from exc


def bbox(ent: Any) -> dict[str, list[float]] | None:
    try:
        mn, mx = ent.GetBoundingBox()
        return {"min": _pt(mn), "max": _pt(mx)}
    except Exception:  # noqa: BLE001
        return None


def lwpoly_points(ent: Any) -> list[tuple[float, float]]:
    name = ent.ObjectName
    coords = ent.Coordinates
    if name == "AcDbPolyline":
        return flat_to_points(coords, 2)
    return flat_to_points(coords, 3)  # AcDb2dPolyline / AcDb3dPolyline


def text_value(ent: Any) -> str:
    try:
        if ent.ObjectName == "AcDbMText":
            # MText TextString carries formatting codes; strip them in a simple way.
            raw = ent.TextString
            return _strip_mtext(raw)
        return ent.TextString
    except Exception:  # noqa: BLE001
        return ""


def _strip_mtext(raw: str) -> str:
    import re

    s = re.sub(r"\\P", " ", raw)
    s = re.sub(r"\\[A-Za-z][^;\\{}]*;", "", s)
    s = re.sub(r"\\[A-Za-z]", "", s)
    s = s.replace("{", "").replace("}", "")
    return " ".join(s.split())


def summary(ent: Any, detail: bool = False) -> dict[str, Any]:
    """JSON-friendly summary of an entity."""
    name = ent.ObjectName
    d: dict[str, Any] = {"handle": ent.Handle, "type": name, "layer": ent.Layer}
    try:
        if name == "AcDbLine":
            d.update(start=_pt(ent.StartPoint), end=_pt(ent.EndPoint), length=round(ent.Length, 4))
        elif name in ("AcDbCircle",):
            d.update(center=_pt(ent.Center), radius=round(ent.Radius, 4), area=round(ent.Area, 4))
        elif name == "AcDbArc":
            d.update(center=_pt(ent.Center), radius=round(ent.Radius, 4), length=round(ent.ArcLength, 4))
        elif name in ("AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"):
            pts = lwpoly_points(ent)
            d.update(closed=bool(ent.Closed), vertices=len(pts), length=round(ent.Length, 4))
            try:
                d["area"] = round(ent.Area, 4)
            except Exception:  # noqa: BLE001
                pass
            if detail:
                d["points"] = [_pt(p) for p in pts]
        elif name in ("AcDbText", "AcDbMText"):
            d.update(text=text_value(ent), insertion=_pt(ent.InsertionPoint), height=round(ent.Height, 4))
        elif name == "AcDbBlockReference":
            d.update(block=effective_block_name(ent), insertion=_pt(ent.InsertionPoint),
                     rotation_deg=round(ent.Rotation * 180 / 3.141592653589793, 2))
            if ent.HasAttributes:
                d["attributes"] = {a.TagString: a.TextString for a in ent.GetAttributes()}
        elif name == "AcDbHatch":
            d.update(pattern=ent.PatternName, area=round(ent.Area, 4))
        elif "Dimension" in name:
            d.update(measurement=round(ent.Measurement, 4), text=ent.TextOverride or "")
    except Exception:  # noqa: BLE001
        pass
    if detail:
        b = bbox(ent)
        if b:
            d["bounds"] = b
        for prop in ("Color", "Linetype", "Lineweight"):
            try:
                d[prop.lower()] = getattr(ent, prop)
            except Exception:  # noqa: BLE001
                pass
    return d


def effective_block_name(ent: Any) -> str:
    """Real block name, even for dynamic blocks (which become *U123 internally)."""
    try:
        return ent.EffectiveName
    except Exception:  # noqa: BLE001
        return ent.Name


def iter_entities(document: Any, where: str = "model") -> Iterable[Any]:
    container = space(document, where)
    count = 0
    for ent in container:
        count += 1
        if count > MAX_SCAN:
            raise AcadError(f"Drawing too large: stopped after {MAX_SCAN} objects. Filter by layer or region.")
        yield ent


def matches(ent: Any, types: set[str] | None, layers: set[str] | None) -> bool:
    try:
        if types and ent.ObjectName not in types:
            return False
        if layers and ent.Layer.upper() not in layers:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


TYPE_ALIASES = {
    "line": "AcDbLine",
    "polyline": "AcDbPolyline", "pline": "AcDbPolyline",
    "circle": "AcDbCircle",
    "arc": "AcDbArc",
    "text": "AcDbText",
    "mtext": "AcDbMText",
    "block": "AcDbBlockReference", "insert": "AcDbBlockReference",
    "hatch": "AcDbHatch",
    "dimension": "AcDbRotatedDimension",
}


def normalize_types(types: list[str] | None) -> set[str] | None:
    if not types:
        return None
    out: set[str] = set()
    for t in types:
        key = t.strip()
        out.add(TYPE_ALIASES.get(key.lower(), key))
        if key.lower() == "dimension":
            out.update({"AcDbAlignedDimension", "AcDbRotatedDimension"})
    return out


def normalize_layers(layers: list[str] | None) -> set[str] | None:
    return {l.strip().upper() for l in layers} if layers else None


def pickfirst(document: Any) -> list[Any]:
    """Objects the user left selected in AutoCAD (active selection)."""
    try:
        ss = document.PickfirstSelectionSet
        return [ss.Item(i) for i in range(ss.Count)]
    except Exception:  # noqa: BLE001
        return []


def get_var(document: Any, name: str) -> Any:
    return document.GetVariable(name)


def wait_for_file(path: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 0 and size == last:
                return
            last = size
        time.sleep(0.5)
    raise AcadError(f"Timed out waiting for {path} to be written.")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)
