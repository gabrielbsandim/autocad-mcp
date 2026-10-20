"""Room areas: compute, label, build the area schedule (table) and export CSV."""
from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files
from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import point_in_polygon
from autocad_mcp.rooms import NO_NAME, collect_rooms
from autocad_mcp.tools import READ, WRITE
from autocad_mcp.units import fmt_num

UNIT_HELP = ("`unit`: drawing unit, 'auto' (reads INSUNITS), 'mm', 'cm' or 'm'. If the drawing "
             "declares no unit, ask the user.")
FILTER_HELP = ("Room outlines are the CLOSED polylines on `layers` (e.g. ['A-AREA']); without it, the "
               "usual room layers (A-AREA, ARQ-___-ARE, AREAS, AMBIENTES) are used and, if none exists, "
               "the tool lists the candidates so you can ask the user. Or pass `handles` / ['selection']. ")
TEXT_ALIGN_MIDDLE_CENTER = 10


def _remove_old_labels(d: Any, rooms: list[dict[str, Any]], layer: str, prefixes: list[str]) -> int:
    """Delete labels a previous run left inside these rooms, so labelling twice does not stack text."""
    outlines = [acad.lwpoly_points(acad.by_handle(d, r["handle"])) for r in rooms]
    doomed = []
    for e in acad.iter_entities(d):
        if acad.kind(e) not in acad.TEXT_TYPES or str(e.Layer).upper() != layer.upper():
            continue
        value = acad.text_value(e)
        if not any(value.startswith(p) for p in prefixes if p):
            continue
        ip = e.InsertionPoint
        if any(point_in_polygon((float(ip[0]), float(ip[1])), pts) for pts in outlines):
            doomed.append(e)
    for e in doomed:
        e.Delete()
    return len(doomed)


def _center(text: Any, x: float, y: float) -> None:
    text.Alignment = TEXT_ALIGN_MIDDLE_CENTER
    text.TextAlignmentPoint = cad.point(x, y)


def _beside_drawing(d: Any, suffix: str) -> str:
    full = d.FullName
    if full:
        return f"{os.path.splitext(full)[0]} - {suffix}"
    return os.path.join(os.path.expanduser("~"), "Documents", f"{os.path.splitext(d.Name)[0]} - {suffix}")


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ, description=(
        "Compute the area (m²) and perimeter (m) of rooms. " + FILTER_HELP
        + "Each room's name comes from the text inside its outline (restrict it with `name_layers`); "
        "integers such as '101' are kept as the room number. The result flags overlapping outlines and "
        "objects that could not be read. " + UNIT_HELP))
    async def room_areas(layers: list[str] | None = None, handles: list[str] | None = None, unit: str = "auto",
                         name_layers: list[str] | None = None) -> str:
        def fn() -> str:
            data = collect_rooms(cad.doc(), layers, handles, unit, name_layers)
            for r in data["rooms"]:
                r.pop("label_point", None)
            data["total_formatted"] = f"{fmt_num(data['total_m2'])} m²"
            return acad.dumps(data)

        return await cad.run(fn)

    @mcp.tool(annotations=WRITE, description=(
        "Write the area (e.g. 'A = 12,50 m²') inside each room, below its name. Labels from an earlier run "
        "(same layer and prefix) are replaced, not stacked. Same filters as room_areas. `text_height` in "
        "drawing units (default: 0.15 m converted). " + UNIT_HELP))
    async def label_room_areas(layers: list[str] | None = None, handles: list[str] | None = None,
                               unit: str = "auto", text_height: float = 0, layer: str = "A-ANNO-TEXT",
                               prefix: str = "A = ", include_name: bool = False) -> str:
        def body(d: Any) -> str:
            data = collect_rooms(d, layers, handles, unit, None)
            rooms = data["rooms"]
            if not rooms:
                raise AcadError("No closed outline found with these filters.")
            h = text_height or 0.15 / data["factor_m"]
            acad.ensure_layer(d, layer)
            names = [r["room"] for r in rooms if include_name and r["room"] != NO_NAME]
            replaced = _remove_old_labels(d, rooms, layer, [prefix, *names])
            for r in rooms:
                cx, cy = r["label_point"]
                if include_name and r["room"] != NO_NAME:
                    t = d.ModelSpace.AddText(r["room"], cad.point(cx, cy + h * 0.8), h)
                    _center(t, cx, cy + h * 0.8)
                    t.Layer = layer
                t = d.ModelSpace.AddText(f"{prefix}{fmt_num(r['area_m2'])} m²", cad.point(cx, cy - h * 0.8), h * 0.8)
                _center(t, cx, cy - h * 0.8)
                t.Layer = layer
            note = f" ({replaced} old label(s) replaced)" if replaced else ""
            return f"{len(rooms)} area label(s) created on layer {layer}{note}. Total: {fmt_num(data['total_m2'])} m²."

        return await acad.change("label_room_areas", {"layers": layers, "handles": handles, "unit": unit,
                                                      "layer": layer}, body)

    @mcp.tool(annotations=WRITE, description=(
        "Create an AREA SCHEDULE (CAD table) with room, area (m²) and perimeter (m), plus the total, "
        "at position [x,y] (top-left corner). Same filters as room_areas. `text_height` in drawing units "
        "(default 0.20 m converted). " + UNIT_HELP))
    async def create_area_table(position: list[float], layers: list[str] | None = None,
                                handles: list[str] | None = None, unit: str = "auto", title: str = "AREA SCHEDULE",
                                text_height: float = 0, include_perimeter: bool = True, layer: str = "A-ANNO-TEXT") -> str:
        def body(d: Any) -> str:
            data = collect_rooms(d, layers, handles, unit, None)
            rooms = data["rooms"]
            if not rooms:
                raise AcadError("No closed outline found with these filters.")
            h = text_height or 0.20 / data["factor_m"]
            headers = ["ROOM", "AREA (m²)"] + (["PERIMETER (m)"] if include_perimeter else [])
            body_rows = [[r["room"], fmt_num(r["area_m2"])] + ([fmt_num(r["perimeter_m"])] if include_perimeter else [])
                         for r in rooms]
            footer = ["TOTAL", fmt_num(data["total_m2"])] + ([""] if include_perimeter else [])
            handle = add_table(d, position, title, headers, [*body_rows, footer], h, layer)
            return f"Area schedule created (handle {handle}) with {len(rooms)} room(s). Total: {fmt_num(data['total_m2'])} m²."

        return await acad.change("create_area_table", {"position": position, "layers": layers, "unit": unit}, body)

    @mcp.tool(annotations=WRITE, description=(
        "Export the area schedule to a CSV file (in pt-BR: ';' separator and decimal comma, so Excel opens "
        "it as numbers). Without `file_path`, saves it next to the drawing as '<drawing> - areas.csv'. "
        + UNIT_HELP))
    async def export_areas_csv(file_path: str = "", layers: list[str] | None = None, handles: list[str] | None = None,
                               unit: str = "auto", overwrite: bool = False) -> str:
        def fn() -> str:
            d = cad.doc()
            path = files.output_path(file_path or _beside_drawing(d, "areas.csv"), extensions={".csv"},
                                     overwrite=overwrite)
            data = collect_rooms(d, layers, handles, unit, None)
            rows = [[r["room"], r["number"], r["area_m2"], r["perimeter_m"], r["layer"], r["handle"]]
                    for r in data["rooms"]]
            rows.append(["TOTAL", "", data["total_m2"], "", "", ""])
            files.write_csv(path, ["Room", "Number", "Area (m²)", "Perimeter (m)", "Layer", "Handle"], rows)
            return f"CSV saved to {path} ({len(data['rooms'])} rooms, total {fmt_num(data['total_m2'])} m²)."

        return await acad.act("export_areas_csv", {"file_path": file_path, "layers": layers}, fn)


def add_table(d: Any, position: list[float], title: str, headers: list[str], rows: list[list[str]],
              h: float, layer: str) -> str:
    """A CAD table with a title row, a header row and the given rows. Returns its handle."""
    if len(position) < 2:
        raise AcadError("The position must be [x, y].")
    ncols = len(headers)
    nrows = len(rows) + 2
    tbl = d.ModelSpace.AddTable(cad.point(*position[:2]), nrows, ncols, h * 2.2, h * 12)
    tbl.SetColumnWidth(0, h * 22)
    for r in range(nrows):
        for c in range(ncols):
            tbl.SetCellTextHeight(r, c, h)
    tbl.SetText(0, 0, title)
    for c, txt in enumerate(headers):
        tbl.SetText(1, c, txt)
    for i, row in enumerate(rows, start=2):
        for c, txt in enumerate(row):
            tbl.SetText(i, c, str(txt))
    acad.ensure_layer(d, layer)
    tbl.Layer = layer
    return str(tbl.Handle)
