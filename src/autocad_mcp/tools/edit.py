"""Editing existing objects, identified by handle or by the user's current selection."""
from __future__ import annotations

import math
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE

HANDLES_HELP = ("`handles`: list of handles (from query_entities/get_selection) or [\"selection\"] "
                "to use the objects the user selected in AutoCAD.")

# Accepted color names (ACI = AutoCAD Color Index).
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


def _vec(offset: list[float], k: float = 1.0) -> Any:
    if len(offset) < 2:
        raise AcadError("The offset must be [dx, dy] or [dx, dy, dz].")
    return cad.point(*(v * k for v in offset[:3]))


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE, description="Move objects by an offset [dx, dy]. " + HANDLES_HELP)
    async def move_entities(handles: list[str], offset: list[float]) -> str:
        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            for e in ents:
                e.Move(cad.point(0, 0), _vec(offset))
            return f"{len(ents)} object(s) moved."

        return await acad.change("move_entities", {"handles": handles, "offset": offset}, body)

    @mcp.tool(annotations=WRITE, description="Copy objects by an offset [dx, dy]; `times` > 1 creates a series of copies (like a linear array). " + HANDLES_HELP)
    async def copy_entities(handles: list[str], offset: list[float], times: int = 1) -> str:
        n = max(1, min(int(times), MAX_COPIES))

        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            new = []
            for k in range(1, n + 1):
                for e in ents:
                    c = e.Copy()
                    c.Move(cad.point(0, 0), _vec(offset, k))
                    new.append(c.Handle)
            more = "..." if len(new) > 50 else ""
            return f"{len(new)} copy(ies) created. Handles: {', '.join(new[:50])}{more}"

        return await acad.change("copy_entities", {"handles": handles, "offset": offset, "times": n}, body)

    @mcp.tool(annotations=WRITE, description="Rotate objects around `base` [x,y] (degrees, counterclockwise). " + HANDLES_HELP)
    async def rotate_entities(handles: list[str], base: list[float], angle_deg: float) -> str:
        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            for e in ents:
                e.Rotate(_vec(base), math.radians(angle_deg))
            return f"{len(ents)} object(s) rotated {angle_deg}°."

        return await acad.change("rotate_entities", {"handles": handles, "base": base, "angle": angle_deg}, body)

    @mcp.tool(annotations=WRITE, description="Scale objects from `base` [x,y] by `factor`. " + HANDLES_HELP)
    async def scale_entities(handles: list[str], base: list[float], factor: float) -> str:
        if factor <= 0:
            raise AcadError("The scale factor must be positive.")

        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            for e in ents:
                e.ScaleEntity(_vec(base), float(factor))
            return f"{len(ents)} object(s) scaled by {factor}."

        return await acad.change("scale_entities", {"handles": handles, "base": base, "factor": factor}, body)

    @mcp.tool(annotations=WRITE, description="Mirror objects across the line p1-p2. keep_original=True keeps the originals. " + HANDLES_HELP)
    async def mirror_entities(handles: list[str], p1: list[float], p2: list[float], keep_original: bool = True) -> str:
        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            new = []
            for e in ents:
                m = e.Mirror(_vec(p1), _vec(p2))
                new.append(m.Handle)
                if not keep_original:
                    e.Delete()
            return f"{len(new)} object(s) mirrored. Handles: {', '.join(new[:50])}"

        return await acad.change("mirror_entities", {"handles": handles, "p1": p1, "p2": p2,
                                                     "keep": keep_original}, body)

    @mcp.tool(annotations=WRITE, description="Create a parallel copy (offset) of lines/polylines/arcs/circles at the given distance; a negative distance goes to the other side. " + HANDLES_HELP)
    async def offset_entities(handles: list[str], distance: float) -> str:
        if distance == 0:
            raise AcadError("The offset distance cannot be zero.")

        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            new = []
            for e in ents:
                new.extend(o.Handle for o in e.Offset(float(distance)))
            return f"{len(new)} offset(s) created. Handles: {', '.join(new[:50])}"

        return await acad.change("offset_entities", {"handles": handles, "distance": distance}, body)

    @mcp.tool(annotations=DESTRUCTIVE, description="DELETES objects from the drawing (can be undone with undo_last). Confirm with the user before deleting many objects. " + HANDLES_HELP)
    async def delete_entities(handles: list[str]) -> str:
        def body(d: Any) -> str:
            ents = acad.resolve_handles(d, handles)
            for e in ents:
                e.Delete()
            return f"{len(ents)} object(s) deleted. Use undo_last to undo."

        return await acad.change("delete_entities", {"handles": handles}, body)

    @mcp.tool(
        annotations=WRITE,
        description="Change object properties: layer, color (a name such as 'red', 'bylayer', or an ACI number), "
        "linetype (e.g. 'CONTINUOUS', 'DASHED', 'ByLayer'), lineweight in mm (e.g. 0.25; -1 = ByLayer), "
        "text (for Text/MText) and text height. " + HANDLES_HELP,
    )
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
