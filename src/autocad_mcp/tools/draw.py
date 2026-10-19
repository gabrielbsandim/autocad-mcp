"""Geometry creation: lines, polylines, walls, text, dimensions, hatches, blocks."""
from __future__ import annotations

import math
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.com import AcadError
from autocad_mcp.tools import WRITE

PointList = list[list[float]]


def _set_layer(ent: Any, d: Any, layer: str) -> None:
    if layer:
        _ensure_layer(d, layer)
        ent.Layer = layer


def _ensure_layer(d: Any, layer: str) -> None:
    try:
        d.Layers.Item(layer)
    except Exception:  # noqa: BLE001
        d.Layers.Add(layer)


def _check_points(points: PointList, minimum: int) -> None:
    if len(points) < minimum:
        raise AcadError(f"At least {minimum} points are required.")
    for p in points:
        if len(p) < 2:
            raise AcadError("Each point must be [x, y] or [x, y, z].")


def _lwpoly(ms: Any, points: PointList, closed: bool) -> Any:
    flat = [c for p in points for c in (p[0], p[1])]
    pl = ms.AddLightWeightPolyline(acad.vflat(flat))
    pl.Closed = bool(closed)
    return pl


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def draw_line(start: list[float], end: list[float], layer: str = "") -> str:
        """Draw a line from `start` [x,y] to `end` [x,y] in model space."""

        def body(d: Any) -> str:
            ent = d.ModelSpace.AddLine(acad.vpoint(*start[:3]), acad.vpoint(*end[:3]))
            _set_layer(ent, d, layer)
            return f"Line created (handle {ent.Handle})."

        return await com.run(acad.write("draw_line", {"start": start, "end": end, "layer": layer}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_polyline(points: PointList, closed: bool = False, layer: str = "") -> str:
        """Draw a polyline through the points [[x,y], ...]. closed=True closes the outline
        (use it for rooms, so their area can be computed later)."""
        _check_points(points, 2)

        def body(d: Any) -> str:
            ent = _lwpoly(d.ModelSpace, points, closed)
            _set_layer(ent, d, layer)
            return f"Polyline created (handle {ent.Handle}, {len(points)} vertices)."

        return await com.run(acad.write("draw_polyline", {"points": points, "closed": closed, "layer": layer}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_rectangle(corner: list[float], width: float, height: float, layer: str = "") -> str:
        """Draw a rectangle (closed polyline) from the lower-left `corner` [x,y], with width
        (X axis) and height (Y axis) in drawing units."""
        x, y = corner[0], corner[1]
        pts = [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]

        def body(d: Any) -> str:
            ent = _lwpoly(d.ModelSpace, pts, True)
            _set_layer(ent, d, layer)
            return f"Rectangle created (handle {ent.Handle})."

        return await com.run(acad.write("draw_rectangle", {"corner": corner, "width": width, "height": height}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_circle(center: list[float], radius: float, layer: str = "") -> str:
        """Draw a circle."""

        def body(d: Any) -> str:
            ent = d.ModelSpace.AddCircle(acad.vpoint(*center[:3]), float(radius))
            _set_layer(ent, d, layer)
            return f"Circle created (handle {ent.Handle})."

        return await com.run(acad.write("draw_circle", {"center": center, "radius": radius}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_arc(center: list[float], radius: float, start_angle_deg: float, end_angle_deg: float,
                       layer: str = "") -> str:
        """Draw an arc (angles in degrees, counterclockwise from the X axis).
        Useful for door swings."""

        def body(d: Any) -> str:
            ent = d.ModelSpace.AddArc(acad.vpoint(*center[:3]), float(radius),
                                      math.radians(start_angle_deg), math.radians(end_angle_deg))
            _set_layer(ent, d, layer)
            return f"Arc created (handle {ent.Handle})."

        return await com.run(acad.write("draw_arc", {"center": center, "radius": radius}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_walls(path: PointList, thickness: float, closed: bool = False, layer: str = "A-WALL") -> str:
        """Draw walls as double lines from the wall CENTERLINE (`path`, [[x,y],...]).
        `thickness` is the total thickness (e.g. 0.15 in meters, or 15 if the drawing is in cm).
        closed=True for a closed outline (e.g. the perimeter of a room)."""
        _check_points(path, 2)
        if thickness <= 0:
            raise AcadError("The thickness must be positive.")

        def body(d: Any) -> str:
            axis = _lwpoly(d.ModelSpace, path, closed)
            handles = []
            try:
                for off in (thickness / 2, -thickness / 2):
                    for o in axis.Offset(off):
                        _set_layer(o, d, layer)
                        handles.append(o.Handle)
            finally:
                axis.Delete()
            return f"Walls created on layer {layer}: {len(handles)} outline(s) (handles {', '.join(handles)})."

        return await com.run(acad.write("draw_walls", {"path": path, "thickness": thickness, "closed": closed}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_text(text: str, position: list[float], height: float, rotation_deg: float = 0,
                        multiline_width: float = 0, layer: str = "") -> str:
        """Write text. If `multiline_width` > 0 it creates an MText (multi-line text that
        wraps at that width); otherwise a single-line text."""

        def body(d: Any) -> str:
            if multiline_width > 0:
                ent = d.ModelSpace.AddMText(acad.vpoint(*position[:3]), float(multiline_width), text)
                ent.Height = float(height)
            else:
                ent = d.ModelSpace.AddText(text, acad.vpoint(*position[:3]), float(height))
            if rotation_deg:
                ent.Rotation = math.radians(rotation_deg)
            _set_layer(ent, d, layer)
            return f"Text created (handle {ent.Handle})."

        return await com.run(acad.write("draw_text", {"text": text, "position": position}, body))

    @mcp.tool(annotations=WRITE)
    async def add_dimension(start: list[float], end: list[float], text_position: list[float],
                            kind: str = "aligned", angle_deg: float = 0, layer: str = "") -> str:
        """Create a dimension between two points. kind='aligned' (aligned to the points) or
        'linear' (horizontal/vertical; use angle_deg=0 for horizontal, 90 for vertical).
        `text_position` sets where the dimension line goes."""

        def body(d: Any) -> str:
            if kind == "linear":
                ent = d.ModelSpace.AddDimRotated(acad.vpoint(*start[:3]), acad.vpoint(*end[:3]),
                                                 acad.vpoint(*text_position[:3]), math.radians(angle_deg))
            else:
                ent = d.ModelSpace.AddDimAligned(acad.vpoint(*start[:3]), acad.vpoint(*end[:3]),
                                                 acad.vpoint(*text_position[:3]))
            _set_layer(ent, d, layer)
            return f"Dimension created (handle {ent.Handle}, measurement {ent.Measurement:.4f})."

        return await com.run(acad.write("add_dimension", {"start": start, "end": end, "kind": kind}, body))

    @mcp.tool(annotations=WRITE)
    async def draw_hatch(boundary: PointList | None = None, boundary_handle: str = "", pattern: str = "SOLID",
                         scale: float = 1.0, angle_deg: float = 0, layer: str = "") -> str:
        """Create a hatch inside a boundary: pass `boundary` (points) OR `boundary_handle`
        (handle of an existing closed polyline). Common patterns: SOLID, ANSI31, AR-CONC,
        AR-SAND, AR-BRSTD, AR-PARQ1, DOTS, GRAVEL."""

        def body(d: Any) -> str:
            temp = None
            if boundary_handle:
                loop = acad.by_handle(d, boundary_handle)
            elif boundary:
                _check_points(boundary, 3)
                loop = temp = _lwpoly(d.ModelSpace, boundary, True)
            else:
                raise AcadError("Pass boundary (points) or boundary_handle.")
            hatch = d.ModelSpace.AddHatch(0, pattern, True)  # 0 = acHatchPatternTypePreDefined
            hatch.AppendOuterLoop(acad.vobjects([loop]))
            if pattern.upper() != "SOLID":
                hatch.PatternScale = float(scale)
                hatch.PatternAngle = math.radians(angle_deg)
            hatch.Evaluate()
            _set_layer(hatch, d, layer)
            if temp is not None:
                temp.Delete()
            return f"Hatch {pattern} created (handle {hatch.Handle}, area {hatch.Area:.4f})."

        return await com.run(acad.write("draw_hatch", {"pattern": pattern, "boundary_handle": boundary_handle}, body))

    @mcp.tool(annotations=WRITE)
    async def insert_block(name: str, position: list[float], scale: float = 1.0, rotation_deg: float = 0,
                           attributes: dict[str, str] | None = None, layer: str = "") -> str:
        """Insert a block already defined in the drawing (see list_blocks) or a .dwg file by its
        full path. `attributes` fills attributes by TAG (e.g. {"NAME": "LIVING ROOM"})."""

        def body(d: Any) -> str:
            ref = d.ModelSpace.InsertBlock(acad.vpoint(*position[:3]), name, float(scale), float(scale),
                                           float(scale), math.radians(rotation_deg))
            filled = 0
            if attributes and ref.HasAttributes:
                wanted = {k.upper(): v for k, v in attributes.items()}
                for att in ref.GetAttributes():
                    if att.TagString.upper() in wanted:
                        att.TextString = wanted[att.TagString.upper()]
                        filled += 1
            _set_layer(ref, d, layer)
            return f"Block {acad.effective_block_name(ref)} inserted (handle {ref.Handle}, {filled} attribute(s) filled)."

        return await com.run(acad.write("insert_block", {"name": name, "position": position}, body))
