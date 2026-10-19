"""Editing existing objects, identified by handle or by the user's current selection."""
from __future__ import annotations

import math
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.com import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE

HANDLES_HELP = ("`handles`: list of handles (from query_entities/get_selection) or [\"selection\"] "
                "to use the objects the user selected in AutoCAD.")

# Accepted color names (ACI = AutoCAD Color Index).
COLORS = {
    "bylayer": 256, "byblock": 0,
    "red": 1, "yellow": 2, "green": 3, "cyan": 4, "blue": 5, "magenta": 6,
    "white": 7, "black": 7, "gray": 8, "light_gray": 9,
}


def color_index(color: str | int) -> int:
    if isinstance(color, int):
        return color
    key = str(color).strip().lower().replace(" ", "_")
    if key in COLORS:
        return COLORS[key]
    try:
        return int(key)
    except ValueError as exc:
        raise AcadError(f"Invalid color {color!r}. Use a number from 1 to 255 or one of: {', '.join(COLORS)}.") from exc


def resolve(d: Any, handles: list[str]) -> list[Any]:
    if not handles:
        raise AcadError("No objects given.")
    if len(handles) == 1 and handles[0].strip().lower() == "selection":
        items = acad.pickfirst(d)
        if not items:
            raise AcadError("Nothing is selected in AutoCAD. Ask the user to select the objects (with no command running).")
        return items
    return [acad.by_handle(d, h) for h in handles]


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE, description="Move objects by an offset [dx, dy]. " + HANDLES_HELP)
    async def move_entities(handles: list[str], offset: list[float]) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            for e in ents:
                e.Move(acad.vpoint(0, 0), acad.vpoint(*offset[:3]))
            return f"{len(ents)} object(s) moved."

        return await com.run(acad.write("move_entities", {"handles": handles, "offset": offset}, body))

    @mcp.tool(annotations=WRITE, description="Copy objects by an offset [dx, dy]; `times` > 1 creates a series of copies (like a linear array). " + HANDLES_HELP)
    async def copy_entities(handles: list[str], offset: list[float], times: int = 1) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            n = max(1, min(int(times), 500))
            new = []
            for k in range(1, n + 1):
                for e in ents:
                    c = e.Copy()
                    c.Move(acad.vpoint(0, 0), acad.vpoint(*(v * k for v in offset[:3])))
                    new.append(c.Handle)
            return f"{len(new)} copy(ies) created. Handles: {', '.join(new[:50])}{'...' if len(new) > 50 else ''}"

        return await com.run(acad.write("copy_entities", {"handles": handles, "offset": offset, "times": times}, body))

    @mcp.tool(annotations=WRITE, description="Rotate objects around `base` [x,y] (degrees, counterclockwise). " + HANDLES_HELP)
    async def rotate_entities(handles: list[str], base: list[float], angle_deg: float) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            for e in ents:
                e.Rotate(acad.vpoint(*base[:3]), math.radians(angle_deg))
            return f"{len(ents)} object(s) rotated {angle_deg}°."

        return await com.run(acad.write("rotate_entities", {"handles": handles, "angle": angle_deg}, body))

    @mcp.tool(annotations=WRITE, description="Scale objects from `base` [x,y] by `factor`. " + HANDLES_HELP)
    async def scale_entities(handles: list[str], base: list[float], factor: float) -> str:
        if factor <= 0:
            raise AcadError("The scale factor must be positive.")

        def body(d: Any) -> str:
            ents = resolve(d, handles)
            for e in ents:
                e.ScaleEntity(acad.vpoint(*base[:3]), float(factor))
            return f"{len(ents)} object(s) scaled by {factor}."

        return await com.run(acad.write("scale_entities", {"handles": handles, "factor": factor}, body))

    @mcp.tool(annotations=WRITE, description="Mirror objects across the line p1–p2. keep_original=True keeps the originals. " + HANDLES_HELP)
    async def mirror_entities(handles: list[str], p1: list[float], p2: list[float], keep_original: bool = True) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            new = []
            for e in ents:
                m = e.Mirror(acad.vpoint(*p1[:3]), acad.vpoint(*p2[:3]))
                new.append(m.Handle)
                if not keep_original:
                    e.Delete()
            return f"{len(new)} object(s) mirrored."

        return await com.run(acad.write("mirror_entities", {"handles": handles, "keep": keep_original}, body))

    @mcp.tool(annotations=WRITE, description="Create a parallel copy (offset) of lines/polylines/arcs/circles at the given distance; a negative distance goes to the other side. " + HANDLES_HELP)
    async def offset_entities(handles: list[str], distance: float) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            new = []
            for e in ents:
                for o in e.Offset(float(distance)):
                    new.append(o.Handle)
            return f"{len(new)} offset(s) created. Handles: {', '.join(new[:50])}"

        return await com.run(acad.write("offset_entities", {"handles": handles, "distance": distance}, body))

    @mcp.tool(annotations=DESTRUCTIVE, description="DELETES objects from the drawing (can be undone with undo_last). Confirm with the user before deleting many objects. " + HANDLES_HELP)
    async def delete_entities(handles: list[str]) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            for e in ents:
                e.Delete()
            return f"{len(ents)} object(s) deleted. Use undo_last to undo."

        return await com.run(acad.write("delete_entities", {"handles": handles}, body))

    @mcp.tool(
        annotations=WRITE,
        description="Change object properties: layer, color (a name such as 'red', 'bylayer', or an ACI number), "
        "linetype (e.g. 'CONTINUOUS', 'DASHED', 'ByLayer'), lineweight in mm (e.g. 0.25; -1 = ByLayer), "
        "text (for Text/MText) and text height. " + HANDLES_HELP,
    )
    async def change_properties(handles: list[str], layer: str = "", color: str = "", linetype: str = "",
                                lineweight_mm: float | None = None, text: str | None = None,
                                text_height: float | None = None) -> str:
        def body(d: Any) -> str:
            ents = resolve(d, handles)
            if layer:
                try:
                    d.Layers.Item(layer)
                except Exception:  # noqa: BLE001
                    d.Layers.Add(layer)
            if linetype and linetype.lower() not in ("bylayer", "byblock"):
                _ensure_linetype(d, linetype)
            changed = 0
            for e in ents:
                if layer:
                    e.Layer = layer
                if color:
                    e.Color = color_index(color)
                if linetype:
                    e.Linetype = linetype
                if lineweight_mm is not None:
                    e.Lineweight = -1 if lineweight_mm < 0 else int(round(lineweight_mm * 100))
                if text is not None and e.ObjectName in ("AcDbText", "AcDbMText"):
                    e.TextString = text
                if text_height is not None and e.ObjectName in ("AcDbText", "AcDbMText"):
                    e.Height = float(text_height)
                changed += 1
            return f"{changed} object(s) changed."

        args = {"handles": handles, "layer": layer, "color": color, "linetype": linetype}
        return await com.run(acad.write("change_properties", args, body))


def _ensure_linetype(d: Any, name: str) -> None:
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
