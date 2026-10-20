"""Editing existing objects, identified by handle or by the user's current selection."""
from __future__ import annotations

import math
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE, spec

HANDLES_HELP = ("`handles`: handles from find_entities, or [\"selection\"] for what the user selected in the CAD.")

COLORS = {
    "bylayer": 256, "byblock": 0,
    "red": 1, "yellow": 2, "green": 3, "cyan": 4, "blue": 5, "magenta": 6,
    "white": 7, "black": 7, "gray": 8, "light_gray": 9,
}

MAX_COPIES = 500


def color_index(color: str | int) -> int:
    if isinstance(color, int):
        value = color
    else:
        key = str(color).strip().lower().replace(" ", "_")
        if key in COLORS:
            return COLORS[key]
        try:
            value = int(key)
        except ValueError as exc:
            raise AcadError(f"Invalid color {color!r}. Use a number from 1 to 255 or one of: {', '.join(COLORS)}.") from exc
    if not 0 <= value <= 256:
        raise AcadError(f"Invalid color {color!r}. Use a number from 1 to 255.")
    return value


def ensure_linetype(d: Any, name: str) -> None:
    try:
        d.Linetypes.Item(name)
        return
    except Exception:  # noqa: BLE001
        pass
    for lin in ("acadiso.lin", "acad.lin"):
        try:
            d.Linetypes.Load(name, lin)
            return
        except Exception:  # noqa: BLE001
            continue
    raise AcadError(f"Linetype {name!r} does not exist and could not be loaded from acadiso.lin.")


def _vec(values: list[float] | None, what: str, k: float = 1.0) -> Any:
    if not values or len(values) < 2:
        raise AcadError(f"`{what}` is required, as [x, y].")
    return cad.point(*(v * k for v in values[:3]))


def _move(ents: list[Any], a: dict[str, Any]) -> str:
    for e in ents:
        e.Move(cad.point(0, 0), _vec(a["offset"], "offset"))
    return f"{len(ents)} object(s) moved."


def _copy(ents: list[Any], a: dict[str, Any]) -> str:
    n = max(1, min(int(a["times"]), MAX_COPIES))
    new = []
    for k in range(1, n + 1):
        for e in ents:
            c = e.Copy()
            c.Move(cad.point(0, 0), _vec(a["offset"], "offset", k))
            new.append(c.Handle)
    more = "..." if len(new) > 50 else ""
    return f"{len(new)} copy(ies) created. Handles: {', '.join(new[:50])}{more}"


def _rotate(ents: list[Any], a: dict[str, Any]) -> str:
    for e in ents:
        e.Rotate(_vec(a["base"], "base"), math.radians(a["angle_deg"]))
    return f"{len(ents)} object(s) rotated {a['angle_deg']}°."


def _scale(ents: list[Any], a: dict[str, Any]) -> str:
    if a["factor"] <= 0:
        raise AcadError("The scale factor must be positive.")
    for e in ents:
        e.ScaleEntity(_vec(a["base"], "base"), float(a["factor"]))
    return f"{len(ents)} object(s) scaled by {a['factor']}."


def _mirror(ents: list[Any], a: dict[str, Any]) -> str:
    new = []
    for e in ents:
        new.append(e.Mirror(_vec(a["p1"], "p1"), _vec(a["p2"], "p2")).Handle)
        if not a["keep_original"]:
            e.Delete()
    return f"{len(new)} object(s) mirrored. Handles: {', '.join(new[:50])}"


def _offset(ents: list[Any], a: dict[str, Any]) -> str:
    if not a["distance"]:
        raise AcadError("The offset distance cannot be zero.")
    new = [o.Handle for e in ents for o in e.Offset(float(a["distance"]))]
    return f"{len(new)} offset(s) created. Handles: {', '.join(new[:50])}"


OPERATIONS = {"move": _move, "copy": _copy, "rotate": _rotate, "scale": _scale, "mirror": _mirror,
              "offset": _offset}


def _apply(e: Any, layer: str, color: int | None, linetype: str, lineweight_mm: float | None,
           text: str | None, text_height: float | None) -> None:
    if layer:
        e.Layer = layer
    if color is not None:
        e.Color = color
    if linetype:
        e.Linetype = linetype
    if lineweight_mm is not None:
        e.Lineweight = acad.lineweight_enum(lineweight_mm)
    if acad.kind(e) not in acad.TEXT_TYPES:
        return
    if text is not None:
        e.TextString = text
    if text_height is not None:
        e.Height = float(text_height)


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Move, copy, rotate, scale, mirror or offset", WRITE, (
        "Change objects geometrically, as one undo step. " + HANDLES_HELP + "\n"
        "- move: by `offset` [dx, dy]. copy: by `offset`; `times` > 1 makes a series (linear array).\n"
        "- rotate: around `base` [x, y] by `angle_deg` (counterclockwise). scale: from `base` by `factor`.\n"
        "- mirror: across the line `p1`-`p2`; keep_original=False removes the originals.\n"
        "- offset: parallel copy of lines, polylines, arcs, circles at `distance` (negative = other side).")))
    async def transform(handles: list[str], operation: Literal["move", "copy", "rotate", "scale", "mirror", "offset"],
                        offset: list[float] | None = None, times: int = 1, base: list[float] | None = None,
                        angle_deg: float = 0, factor: float = 1.0, p1: list[float] | None = None,
                        p2: list[float] | None = None, keep_original: bool = True, distance: float = 0) -> str:
        args = {"offset": offset, "times": times, "base": base, "angle_deg": angle_deg, "factor": factor,
                "p1": p1, "p2": p2, "keep_original": keep_original, "distance": distance}

        def body(d: Any) -> str:
            return OPERATIONS[operation](acad.resolve_handles(d, handles), args)

        return await acad.change("transform", {"handles": handles, "operation": operation, **args}, body)

    @mcp.tool(**spec("Delete objects", DESTRUCTIVE, "DELETES objects from the drawing (undo brings them back). "
                     "Confirm with the user before deleting many objects. " + HANDLES_HELP))
    async def delete_entities(handles: list[str]) -> str:
        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            for e in ents:
                e.Delete()
            return f"{len(ents)} object(s) deleted. Use undo to bring them back."

        return await acad.change("delete_entities", {"handles": handles}, body)

    @mcp.tool(**spec("Change properties", WRITE, "Change object properties: layer, color (a name such as 'red', "
                     "'bylayer', or an ACI number), linetype (e.g. 'CONTINUOUS', 'DASHED', 'ByLayer'), lineweight "
                     "in mm (e.g. 0.25; -1 = ByLayer), text (Text/MText) and text height. " + HANDLES_HELP))
    async def change_properties(handles: list[str], layer: str = "", color: str = "", linetype: str = "",
                                lineweight_mm: float | None = None, text: str | None = None,
                                text_height: float | None = None) -> str:
        color_value = color_index(color) if color else None
        if text_height is not None and text_height <= 0:
            raise AcadError("The text height must be positive.")

        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            if layer:
                acad.ensure_layer(d, layer)
            if linetype and linetype.lower() not in ("bylayer", "byblock"):
                ensure_linetype(d, linetype)
            for e in ents:
                _apply(e, layer, color_value, linetype, lineweight_mm, text, text_height)
            return f"{len(ents)} object(s) changed."

        args = {"handles": handles, "layer": layer, "color": color, "linetype": linetype,
                "lineweight_mm": lineweight_mm, "text": text, "text_height": text_height}
        return await acad.change("change_properties", args, body)
