"""Helpers on top of the AutoCAD object model (they run on the CAD thread)."""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from autocad_mcp import cad
from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import flat_to_points

READ_ONLY = os.environ.get("AUTOCAD_MCP_READONLY", "").strip().lower() in ("1", "true", "yes")

LOG_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state") / "autocad-mcp"
LOG_FILE = LOG_DIR / "actions.log"

# Maximum number of entities scanned per query, so huge drawings don't hang the server.
MAX_SCAN = int(os.environ.get("AUTOCAD_MCP_MAX_SCAN", "200000"))

VALID_LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158,
                     200, 211]
LW_BYLAYER, LW_BYBLOCK, LW_DEFAULT = -1, -2, -3

DIMENSION_TYPES = {
    "AcDbAlignedDimension", "AcDbRotatedDimension", "AcDbRadialDimension", "AcDbDiametricDimension",
    "AcDbAngularDimension", "AcDb2LineAngularDimension", "AcDb3PointAngularDimension",
    "AcDbOrdinateDimension", "AcDbArcDimension", "AcDbRadialDimensionLarge",
}

TYPE_ALIASES: dict[str, set[str]] = {
    "line": {"AcDbLine"},
    "polyline": {"AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"},
    "pline": {"AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"},
    "circle": {"AcDbCircle"},
    "arc": {"AcDbArc"},
    "spline": {"AcDbSpline"},
    "ellipse": {"AcDbEllipse"},
    "text": {"AcDbText"},
    "mtext": {"AcDbMText"},
    "block": {"AcDbBlockReference"},
    "insert": {"AcDbBlockReference"},
    "hatch": {"AcDbHatch"},
    "dimension": DIMENSION_TYPES,
    "table": {"AcDbTable"},
}

TEXT_TYPES = {"AcDbText", "AcDbMText"}
POLY_TYPES = {"AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"}

_FOREIGN_PREFIX = re.compile(r"^[A-Z][a-z]Db")


def _pt(p: Sequence[float]) -> list[float]:
    return [round(float(v), 4) for v in p]


def kind(ent: Any) -> str:
    """ObjectName with other CADs' class prefixes (ZcDb, GcDb, OdDb) mapped to AutoCAD's AcDb."""
    name = str(ent.ObjectName)
    if _FOREIGN_PREFIX.match(name) and not name.startswith("AcDb"):
        return "AcDb" + name[4:]
    return name


def lineweight_enum(mm: float) -> int:
    """0.25 mm -> 25, snapped to the closest value AutoCAD accepts; negative means ByLayer."""
    if mm < 0:
        return LW_BYLAYER
    target = mm * 100
    return min(VALID_LINEWEIGHTS, key=lambda v: abs(v - target))


# ---------------------------------------------------------------- log / writes

def log_action(tool: str, args: dict[str, Any], result: str, ok: bool = True) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            name = cad.doc().Name
        except Exception:  # noqa: BLE001
            name = "?"
        line = {
            "when": datetime.now().isoformat(timespec="seconds"),
            "backend": cad.backend().name,
            "drawing": name,
            "tool": tool,
            "ok": ok,
            "args": args,
            "result": str(result)[:500],
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break an action
        pass


def ensure_writable() -> None:
    if READ_ONLY:
        raise AcadError(
            "The server is in READ-ONLY mode (AUTOCAD_MCP_READONLY=1). Nothing was changed or saved. "
            "To allow changes, remove that option from the Claude configuration."
        )


def _end_group(document: Any) -> None:
    try:
        document.EndUndoMark()
    except Exception:  # noqa: BLE001
        pass


def _rollback(document: Any) -> bool:
    try:
        cad.backend().undo(document, 1)
        return True
    except Exception:  # noqa: BLE001
        return False


def write(tool: str, args: dict[str, Any], fn: Callable[[Any], str]) -> Callable[[], str]:
    """Wrap a drawing change: refuse in read-only mode, wait for an idle CAD, group the change
    into one undo step, undo it entirely if it fails halfway, and log it either way."""

    def runner() -> str:
        ensure_writable()
        cad.wait_until_ready()
        document = cad.doc()
        document.StartUndoMark()
        try:
            result = fn(document)
        except Exception as exc:  # noqa: BLE001
            _end_group(document)
            rolled_back = _rollback(document)
            err = cad.friendly(exc)
            tail = ("Nothing was changed." if rolled_back
                    else "Part of the change may have been applied: check the drawing or use undo_last.")
            log_action(tool, args, f"{err} {tail}", ok=False)
            raise AcadError(f"{err} {tail}") from exc
        _end_group(document)
        log_action(tool, args, result)
        return result

    return runner


def logged(tool: str, args: dict[str, Any], fn: Callable[[], str], *, writes: bool = True) -> Callable[[], str]:
    """Wrap an action that is not a drawing edit (open, save, export): read-only check and log."""

    def runner() -> str:
        if writes:
            ensure_writable()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            err = cad.friendly(exc)
            log_action(tool, args, str(err), ok=False)
            raise err from exc
        log_action(tool, args, result)
        return result

    return runner


async def change(tool: str, args: dict[str, Any], body: Callable[[Any], str]) -> str:
    return await cad.run(write(tool, args, body), retry=False)


async def act(tool: str, args: dict[str, Any], fn: Callable[[], str], *, writes: bool = True,
              limit: float | None = None) -> str:
    return await cad.run(logged(tool, args, fn, writes=writes), retry=False, limit=limit)


# ---------------------------------------------------------------- scanning

class Scan:
    """Counts objects a scan could not read, instead of silently leaving them out of totals."""

    def __init__(self) -> None:
        self.skipped = 0
        self.reasons: Counter[str] = Counter()

    @contextmanager
    def item(self) -> Iterator[None]:
        try:
            yield
        except AcadError:
            raise
        except Exception as exc:  # noqa: BLE001
            if cad.backend().is_fatal(exc):
                raise
            self.skipped += 1
            self.reasons[str(exc)[:120]] += 1

    def report(self, into: dict[str, Any]) -> dict[str, Any]:
        if self.skipped:
            into["complete"] = False
            into["skipped_objects"] = self.skipped
            into["skipped_reasons"] = dict(self.reasons.most_common(3))
            into["warning"] = (f"{self.skipped} object(s) could not be read and are NOT in these results. "
                               "Tell the user the numbers may be incomplete.")
        return into


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
    except Exception:  # noqa: BLE001
        return None
    return {"min": _pt(mn), "max": _pt(mx)}


def lwpoly_points(ent: Any) -> list[tuple[float, float]]:
    coords = ent.Coordinates
    if kind(ent) == "AcDbPolyline":
        return flat_to_points(coords, 2)
    return flat_to_points(coords, 3)


def text_value(ent: Any) -> str:
    if kind(ent) == "AcDbMText":
        return strip_mtext(ent.TextString)
    return str(ent.TextString)


def strip_mtext(raw: str) -> str:
    s = re.sub(r"\\P", " ", raw)
    s = re.sub(r"\\[A-Za-z][^;\\{}]*;", "", s)
    s = re.sub(r"\\[A-Za-z]", "", s)
    s = s.replace("{", "").replace("}", "")
    return " ".join(s.split())


def curve_length(ent: Any) -> tuple[float, bool]:
    """(length, exact). Splines have no Length over COM, so the fit or control points stand in."""
    t = kind(ent)
    if t == "AcDbArc":
        return float(ent.ArcLength), True
    if t == "AcDbCircle":
        return float(ent.Circumference), True
    try:
        return float(ent.Length), True
    except Exception:  # noqa: BLE001
        if t != "AcDbSpline":
            raise
    points = _spline_points(ent)
    total = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    return total, False


def _spline_points(ent: Any) -> list[tuple[float, float]]:
    fit = int(getattr(ent, "NumberOfFitPoints", 0) or 0)
    coords = ent.FitPoints if fit else ent.ControlPoints
    return flat_to_points(coords, 3)


def summary(ent: Any, detail: bool = False) -> dict[str, Any]:
    """JSON-friendly summary of an entity."""
    name = kind(ent)
    d: dict[str, Any] = {"handle": ent.Handle, "type": name, "layer": ent.Layer}
    try:
        _describe(ent, name, d, detail)
    except Exception as exc:  # noqa: BLE001
        d["unreadable"] = str(exc)[:120]
    if detail:
        b = bbox(ent)
        if b:
            d["bounds"] = b
        for prop in ("Color", "Linetype", "Lineweight"):
            try:
                d[prop.lower()] = getattr(ent, prop)
            except Exception:  # noqa: BLE001
                continue
    return d


def _describe(ent: Any, name: str, d: dict[str, Any], detail: bool) -> None:
    if name == "AcDbLine":
        d.update(start=_pt(ent.StartPoint), end=_pt(ent.EndPoint), length=round(ent.Length, 4))
        return
    if name == "AcDbCircle":
        d.update(center=_pt(ent.Center), radius=round(ent.Radius, 4), area=round(ent.Area, 4))
        return
    if name == "AcDbArc":
        d.update(center=_pt(ent.Center), radius=round(ent.Radius, 4), length=round(ent.ArcLength, 4))
        return
    if name in POLY_TYPES:
        pts = lwpoly_points(ent)
        d.update(closed=bool(ent.Closed), vertices=len(pts), length=round(ent.Length, 4))
        if ent.Closed:
            d["area"] = round(ent.Area, 4)
        if detail:
            d["points"] = [_pt(p) for p in pts]
        return
    if name in TEXT_TYPES:
        d.update(text=text_value(ent), insertion=_pt(ent.InsertionPoint), height=round(ent.Height, 4))
        return
    if name == "AcDbBlockReference":
        d.update(block=effective_block_name(ent), insertion=_pt(ent.InsertionPoint),
                 rotation_deg=round(math.degrees(ent.Rotation), 2))
        if ent.HasAttributes:
            d["attributes"] = {a.TagString: a.TextString for a in ent.GetAttributes()}
        return
    if name == "AcDbHatch":
        d.update(pattern=ent.PatternName, area=round(ent.Area, 4))
        return
    if name == "AcDbSpline":
        length, exact = curve_length(ent)
        d.update(length=round(length, 4), length_exact=exact)
        return
    if name in DIMENSION_TYPES:
        d.update(measurement=round(ent.Measurement, 4), text=ent.TextOverride or "")


def effective_block_name(ent: Any) -> str:
    """Real block name, even for dynamic blocks (which become *U123 internally)."""
    try:
        return str(ent.EffectiveName)
    except Exception:  # noqa: BLE001
        return str(ent.Name)


def iter_entities(document: Any, where: str = "model") -> Iterable[Any]:
    container = space(document, where)
    count = 0
    for ent in container:
        count += 1
        if count > MAX_SCAN:
            raise AcadError(f"Drawing too large: stopped after {MAX_SCAN} objects. Filter by layer or region.")
        yield ent


def matches(ent: Any, types: set[str] | None, layers: set[str] | None) -> bool:
    if types and kind(ent) not in types:
        return False
    return not (layers and str(ent.Layer).upper() not in layers)


def normalize_types(types: list[str] | None) -> set[str] | None:
    if not types:
        return None
    out: set[str] = set()
    for t in types:
        key = t.strip()
        out |= TYPE_ALIASES.get(key.lower(), {key})
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


def resolve_handles(document: Any, handles: list[str]) -> list[Any]:
    """Objects by handle, or the user's current selection for ["selection"]."""
    if not handles:
        raise AcadError("No objects given.")
    if len(handles) == 1 and handles[0].strip().lower() == "selection":
        items = pickfirst(document)
        if not items:
            raise AcadError("Nothing is selected in AutoCAD. Ask the user to select the objects (with no "
                            "command running), or pass handles from find_entities.")
        return items
    return [by_handle(document, h) for h in handles]


def inside_region(ent: Any, region: list[list[float]] | None) -> bool:
    """True when the object's extents sit entirely inside the rectangle [[x1,y1],[x2,y2]]."""
    if not region:
        return True
    b = bbox(ent)
    if not b:
        return False
    (x1, y1), (x2, y2) = region[0][:2], region[1][:2]
    lo_x, hi_x, lo_y, hi_y = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
    return b["min"][0] >= lo_x and b["max"][0] <= hi_x and b["min"][1] >= lo_y and b["max"][1] <= hi_y


def ensure_layer(document: Any, name: str) -> Any:
    try:
        return document.Layers.Item(name)
    except Exception:  # noqa: BLE001
        return document.Layers.Add(name)


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
