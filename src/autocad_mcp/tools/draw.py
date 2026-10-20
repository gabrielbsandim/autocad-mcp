"""Geometry creation in one call: lines, polylines, walls, text, dimensions, hatches and blocks."""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from autocad_mcp import acad, cad, files
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import WRITE, spec

Point = Annotated[list[float], Field(min_length=2, max_length=3)]
Positive = Annotated[float, Field(gt=0)]

MAX_ITEMS = 500


def _lean_schema(schema: dict[str, Any]) -> None:
    """Drop the auto-generated titles: ten item types repeat them and Claude pays for every token."""
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)


class _Item(BaseModel):
    model_config = ConfigDict(json_schema_extra=_lean_schema)

    layer: str = ""


class Line(_Item):
    shape: Literal["line"]
    start: Point
    end: Point


class Polyline(_Item):
    shape: Literal["polyline"]
    points: Annotated[list[Point], Field(min_length=2)]
    closed: bool = Field(False, description="True for room outlines, so their area can be computed.")


class Rectangle(_Item):
    shape: Literal["rectangle"]
    corner: Point = Field(description="Lower-left corner.")
    width: Positive
    height: Positive


class Circle(_Item):
    shape: Literal["circle"]
    center: Point
    radius: Positive


class Arc(_Item):
    shape: Literal["arc"]
    center: Point
    radius: Positive
    start_angle_deg: float
    end_angle_deg: float = Field(description="Counterclockwise from the X axis. Door swings are arcs.")


class Walls(_Item):
    shape: Literal["walls"]
    path: Annotated[list[Point], Field(min_length=2, description="Wall CENTERLINE.")]
    thickness: Positive = Field(description="Total thickness in drawing units (0.15 in m, 15 in cm).")
    closed: bool = False


class Text(_Item):
    shape: Literal["text"]
    text: str
    position: Point
    height: Positive
    rotation_deg: float = 0
    multiline_width: float = Field(0, ge=0, description="> 0 makes an MText wrapping at this width.")


class Dimension(_Item):
    shape: Literal["dimension"]
    start: Point
    end: Point
    text_position: Point = Field(description="Where the dimension line goes.")
    kind: Literal["aligned", "linear"] = "aligned"
    angle_deg: float = Field(0, description="For linear: 0 horizontal, 90 vertical.")


class Hatch(_Item):
    shape: Literal["hatch"]
    boundary: Annotated[list[Point], Field(min_length=3)] | None = None
    boundary_handle: str = Field("", description="An existing closed polyline, instead of boundary points.")
    pattern: str = Field("SOLID", description="SOLID, ANSI31, AR-CONC, AR-SAND, AR-BRSTD, AR-PARQ1, DOTS, GRAVEL...")
    scale: Positive = 1.0
    angle_deg: float = 0


class Block(_Item):
    shape: Literal["block"]
    name: str = Field(description="A block defined in the drawing, or the full path of a .dwg/.dxf.")
    position: Point
    scale: Positive = 1.0
    rotation_deg: float = 0
    attributes: dict[str, str] | None = Field(None, description='By TAG, e.g. {"TIPO": "P1"}.')


Shape = Annotated[Union[Line, Polyline, Rectangle, Circle, Arc, Walls, Text, Dimension, Hatch, Block],
                  Field(discriminator="shape")]


def _p(p: list[float]) -> Any:
    return cad.point(*p[:3])


def _lwpoly(ms: Any, points: list[list[float]], closed: bool) -> Any:
    pl = ms.AddLightWeightPolyline(cad.flat([c for p in points for c in (p[0], p[1])]))
    pl.Closed = bool(closed)
    return pl


def _line(ms: Any, d: Any, it: Line) -> list[Any]:
    return [ms.AddLine(_p(it.start), _p(it.end))]


def _polyline(ms: Any, d: Any, it: Polyline) -> list[Any]:
    return [_lwpoly(ms, it.points, it.closed)]


def _rectangle(ms: Any, d: Any, it: Rectangle) -> list[Any]:
    x, y = it.corner[0], it.corner[1]
    pts = [[x, y], [x + it.width, y], [x + it.width, y + it.height], [x, y + it.height]]
    return [_lwpoly(ms, pts, True)]


def _circle(ms: Any, d: Any, it: Circle) -> list[Any]:
    return [ms.AddCircle(_p(it.center), it.radius)]


def _arc(ms: Any, d: Any, it: Arc) -> list[Any]:
    return [ms.AddArc(_p(it.center), it.radius, math.radians(it.start_angle_deg), math.radians(it.end_angle_deg))]


def _walls(ms: Any, d: Any, it: Walls) -> list[Any]:
    axis = _lwpoly(ms, it.path, it.closed)
    try:
        return [o for off in (it.thickness / 2, -it.thickness / 2) for o in axis.Offset(off)]
    finally:
        axis.Delete()


def _text(ms: Any, d: Any, it: Text) -> list[Any]:
    if it.multiline_width > 0:
        ent = ms.AddMText(_p(it.position), it.multiline_width, it.text)
        ent.Height = it.height
    else:
        ent = ms.AddText(it.text, _p(it.position), it.height)
    if it.rotation_deg:
        ent.Rotation = math.radians(it.rotation_deg)
    return [ent]


def _dimension(ms: Any, d: Any, it: Dimension) -> list[Any]:
    if it.kind == "linear":
        return [ms.AddDimRotated(_p(it.start), _p(it.end), _p(it.text_position), math.radians(it.angle_deg))]
    return [ms.AddDimAligned(_p(it.start), _p(it.end), _p(it.text_position))]


def _hatch(ms: Any, d: Any, it: Hatch) -> list[Any]:
    if not it.boundary_handle and not it.boundary:
        raise AcadError("A hatch needs boundary points or a boundary_handle.")
    temp = None if it.boundary_handle else _lwpoly(ms, it.boundary or [], True)
    loop = acad.by_handle(d, it.boundary_handle) if it.boundary_handle else temp
    hatch = ms.AddHatch(0, it.pattern, True)
    hatch.AppendOuterLoop(cad.objects([loop]))
    if it.pattern.upper() != "SOLID":
        hatch.PatternScale = it.scale
        hatch.PatternAngle = math.radians(it.angle_deg)
    hatch.Evaluate()
    if temp is not None:
        temp.Delete()
    return [hatch]


def _block(ms: Any, d: Any, it: Block) -> list[Any]:
    source = it.name
    if source.lower().endswith((".dwg", ".dxf")):
        source = files.safe_path(source, extensions={".dwg", ".dxf"}, must_exist=True)
    ref = ms.InsertBlock(_p(it.position), source, it.scale, it.scale, it.scale, math.radians(it.rotation_deg))
    if it.attributes and ref.HasAttributes:
        wanted = {k.upper(): v for k, v in it.attributes.items()}
        for att in ref.GetAttributes():
            if att.TagString.upper() in wanted:
                att.TextString = wanted[att.TagString.upper()]
    return [ref]


MAKERS = {
    "line": _line, "polyline": _polyline, "rectangle": _rectangle, "circle": _circle, "arc": _arc,
    "walls": _walls, "text": _text, "dimension": _dimension, "hatch": _hatch, "block": _block,
}


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Draw", WRITE))
    async def draw(items: Annotated[list[Shape], Field(min_length=1, max_length=MAX_ITEMS)], layer: str = "") -> str:
        """Draw one or many items in model space, all as ONE undo step. Each item has a `shape`:
        line, polyline, rectangle, circle, arc, walls (double line from the centerline), text,
        dimension, hatch or block (insert, with attributes). Points are [x, y] or [x, y, z]; coordinates
        and sizes are in drawing units. An item's `layer` wins over the call's `layer`; with neither, the
        current layer is used. Missing layers are created."""

        def body(d: Any) -> str:
            ms = d.ModelSpace
            made: list[str] = []
            for it in items:
                target = it.layer or layer
                for ent in MAKERS[it.shape](ms, d, it):
                    if target:
                        acad.ensure_layer(d, target)
                        ent.Layer = target
                    made.append(f"{it.shape} {ent.Handle}")
            more = " ..." if len(made) > 60 else ""
            return f"{len(made)} object(s) created: {', '.join(made[:60])}{more}"

        return await acad.change("draw", {"items": [i.model_dump() for i in items], "layer": layer}, body)
