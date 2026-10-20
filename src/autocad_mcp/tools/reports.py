"""Room areas and quantity takeoffs: read them, label rooms, place them as a table, save them as CSV."""
from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files, reports
from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import point_in_polygon
from autocad_mcp.rooms import NO_NAME, collect_rooms
from autocad_mcp.tools import READ, WRITE, spec
from autocad_mcp.units import fmt_num

UNIT_HELP = ("`unit`: drawing unit, 'auto' (reads INSUNITS), 'mm', 'cm' or 'm'; if the drawing declares no "
             "unit, ask the user.")
ROOMS_HELP = ("Room outlines are the CLOSED polylines on `layers` (e.g. ['A-AREA']); without it, the usual room "
              "layers (A-AREA, ARQ-___-ARE, AREAS, AMBIENTES) are used and, if none exists, the error lists the "
              "candidates so you can ask the user. Or pass `handles` / ['selection']. ")
Report = Literal["rooms", "blocks", "lengths", "openings", "site"]
SiteLayers = dict[str, list[str]] | None
Floors = dict[str, list[list[float]]] | None
Limits = dict[str, float] | None
SITE_HELP = ("Site: closed outlines (or hatches) on the lot, built-area, projection and permeable layers; usual "
             "names are found by themselves, otherwise the error lists candidates. `site_layers` "
             "{'lot': [...], 'built': [...], 'projection': [...], 'permeable': [...]} overrides them; `floors` "
             "{'TÉRREO': [[x1,y1],[x2,y2]], ...} splits the built area by the region each floor plan sits in; "
             "`limits` {'TO': 60, 'CA': 2, 'TP': 15} checks the zoning limits (TO and TP in %).")
TEXT_ALIGN_MIDDLE_CENTER = 10


def _filters(layers: list[str] | None, handles: list[str] | None, names: list[str] | None,
             region: list[list[float]] | None, group_by_attribute: str, unit: str, site_layers: SiteLayers = None,
             floors: Floors = None, limits: Limits = None) -> reports.Filters:
    return reports.Filters(layers=layers, handles=handles, names=names, region=region,
                           group_by_attribute=group_by_attribute, unit=unit, site_layers=site_layers,
                           floors=floors, limits=limits)


def _remove_old_labels(d: Any, rooms: list[dict[str, Any]], layer: str, prefixes: list[str]) -> int:
    """Delete labels a previous run left inside these rooms, so labelling twice does not stack text."""
    outlines = [acad.lwpoly_points(acad.by_handle(d, r["handle"])) for r in rooms]
    doomed = []
    for e in acad.iter_entities(d):
        if acad.kind(e) not in acad.TEXT_TYPES or str(e.Layer).upper() != layer.upper():
            continue
        if not any(acad.text_value(e).startswith(p) for p in prefixes if p):
            continue
        ip = e.InsertionPoint
        if any(point_in_polygon((float(ip[0]), float(ip[1])), pts) for pts in outlines):
            doomed.append(e)
    for e in doomed:
        e.Delete()
    return len(doomed)


def _centered_text(d: Any, text: str, x: float, y: float, h: float, layer: str) -> None:
    t = d.ModelSpace.AddText(text, cad.point(x, y), h)
    t.Alignment = TEXT_ALIGN_MIDDLE_CENTER
    t.TextAlignmentPoint = cad.point(x, y)
    t.Layer = layer


def _beside_drawing(d: Any, suffix: str) -> str:
    full = d.FullName
    if full:
        return f"{os.path.splitext(full)[0]} - {suffix}"
    return os.path.join(os.path.expanduser("~"), "Documents", f"{os.path.splitext(d.Name)[0]} - {suffix}")


def _cell(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return fmt_num(value)
    return str(value)


def add_table(d: Any, position: list[float], schedule: reports.Schedule, h: float, layer: str,
              title: str = "") -> str:
    """A CAD table with a title row, a header row, the rows and the footer. Returns its handle."""
    if len(position) < 2:
        raise AcadError("The position must be [x, y].")
    rows = [*schedule.rows, *([schedule.footer] if schedule.footer else [])]
    if not schedule.rows:
        raise AcadError("The report is empty, so there is no table to place.")
    ncols = len(schedule.columns)
    nrows = len(rows) + 2
    tbl = d.ModelSpace.AddTable(cad.point(*position[:2]), nrows, ncols, h * 2.2, h * 12)
    tbl.SetColumnWidth(0, h * 22)
    for r in range(nrows):
        for c in range(ncols):
            tbl.SetCellTextHeight(r, c, h)
    tbl.SetText(0, 0, title or schedule.title)
    for c, txt in enumerate(schedule.columns):
        tbl.SetText(1, c, txt)
    for i, row in enumerate(rows, start=2):
        for c, value in enumerate(row):
            tbl.SetText(i, c, _cell(value))
    acad.ensure_layer(d, layer)
    tbl.Layer = layer
    return str(tbl.Handle)


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Room areas", READ, (
        "Area (m²) and perimeter (m) of each room, and the total. " + ROOMS_HELP
        + "A room's name comes from the text inside its outline (limit with `name_layers`); integers such as "
        "'101' are kept as the room number. The result flags overlapping outlines and objects that could not be "
        "read. " + UNIT_HELP)))
    async def room_areas(layers: list[str] | None = None, handles: list[str] | None = None, unit: str = "auto",
                         name_layers: list[str] | None = None) -> str:
        def fn() -> str:
            data = collect_rooms(cad.doc(), layers, handles, unit, name_layers)
            for r in data["rooms"]:
                r.pop("label_point", None)
            data["total_formatted"] = f"{fmt_num(data['total_m2'])} m²"
            return acad.dumps(data)

        return await cad.run(fn)

    @mcp.tool(**spec("Quantity takeoff", READ))
    async def takeoff(kind: Literal["blocks", "lengths", "openings"] = "blocks", names: list[str] | None = None,
                      layers: list[str] | None = None, region: list[list[float]] | None = None,
                      group_by_attribute: str = "", unit: str = "auto") -> str:
        """Quantities from the drawing.
        - kind='blocks': count doors, windows, fixtures, furniture... `names` filters by block name with
          wildcards (["PORTA*", "DOOR*"]); `group_by_attribute` splits by an attribute value (e.g. "TIPO" -> P1, P2).
        - kind='lengths': per layer, total length (m) of lines, polylines, arcs, circles, splines and ellipses,
          and hatch area (m²): baseboards, thresholds, wall centerlines, hatched floors.
        - kind='openings': the door and window schedule (quadro de esquadrias): doors and windows found by
          block name or layer (PORTA*, JANELA*, ARQ-POR, ARQ-CXO, A-DOOR, A-GLAZ...), grouped by code (TIPO,
          CODIGO... or `group_by_attribute`) with width, height and sill from attributes (LARGURA, ALTURA,
          PEITORIL or '80x210'); without a width attribute, the width is measured from the block.
        `names`, `layers` and `region` [[x1,y1],[x2,y2]] (e.g. one floor) narrow all three. `unit` as in
        room_areas."""

        def fn() -> str:
            f = _filters(layers, None, names, region, group_by_attribute, unit)
            return acad.dumps(reports.build(kind, cad.doc(), f).data)

        return await cad.run(fn)

    @mcp.tool(**spec("Site areas and zoning indices", READ, (
        "The site area table for the building permit: lot area, built area (per floor), projection, permeable "
        "area, and the indices TO (projection / lot), CA (built / lot) and TP (permeable / lot). " + SITE_HELP
        + " " + UNIT_HELP)))
    async def site_areas(site_layers: SiteLayers = None, floors: Floors = None, limits: Limits = None,
                         unit: str = "auto") -> str:
        def fn() -> str:
            return acad.dumps(reports.build("site", cad.doc(), _filters(None, None, None, None, "", unit,
                                                                       site_layers, floors, limits)).data)

        return await cad.run(fn)

    @mcp.tool(**spec("Label rooms", WRITE, (
        "Write the area (e.g. 'A = 12,50 m²') inside each room, below its name when include_name=True. Labels "
        "from an earlier run (same layer and prefix) are replaced, not stacked; a U or L shaped room gets its "
        "label inside it. Same filters as room_areas. `text_height` in drawing units (default 0.15 m). "
        + UNIT_HELP)))
    async def label_rooms(layers: list[str] | None = None, handles: list[str] | None = None, unit: str = "auto",
                          text_height: float = 0, layer: str = "A-ANNO-TEXT", prefix: str = "A = ",
                          include_name: bool = False) -> str:
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
                    _centered_text(d, r["room"], cx, cy + h * 0.8, h, layer)
                _centered_text(d, f"{prefix}{fmt_num(r['area_m2'])} m²", cx, cy - h * 0.8, h * 0.8, layer)
            note = f" ({replaced} old label(s) replaced)" if replaced else ""
            return f"{len(rooms)} area label(s) created on layer {layer}{note}. Total: {fmt_num(data['total_m2'])} m²."

        return await acad.change("label_rooms", {"layers": layers, "handles": handles, "unit": unit,
                                                 "layer": layer, "include_name": include_name}, body)

    @mcp.tool(**spec("Place schedule table", WRITE))
    async def place_table(report: Report, position: list[float], title: str = "", layers: list[str] | None = None,
                          handles: list[str] | None = None, names: list[str] | None = None,
                          region: list[list[float]] | None = None, group_by_attribute: str = "",
                          unit: str = "auto", site_layers: SiteLayers = None, floors: Floors = None,
                          limits: Limits = None, text_height: float = 0, layer: str = "A-ANNO-TEXT") -> str:
        """Insert a schedule as a CAD table with its top-left corner at `position` [x, y]:
        report='rooms' (area schedule, filters as room_areas), 'blocks', 'lengths' or 'openings' (filters
        as takeoff) or 'site' (filters as site_areas). Titles and headers follow the number format (pt-BR:
        QUADRO DE ÁREAS, QUADRO DE ESQUADRIAS...). `text_height` in drawing units (default 0.20 m)."""

        def body(d: Any) -> str:
            schedule = reports.build(report, d, _filters(layers, handles, names, region, group_by_attribute, unit,
                                                         site_layers, floors, limits))
            factor = schedule.data.get("factor_m") or 1.0
            h = text_height or 0.20 / factor
            handle = add_table(d, position, schedule, h, layer, title)
            return f"Table created (handle {handle}) with {len(schedule.rows)} row(s)."

        return await acad.change("place_table", {"report": report, "position": position, "layers": layers}, body)

    @mcp.tool(**spec("Export schedule to CSV", WRITE))
    async def export_csv(report: Report, file_path: str = "", overwrite: bool = False,
                         layers: list[str] | None = None, handles: list[str] | None = None,
                         names: list[str] | None = None, region: list[list[float]] | None = None,
                         group_by_attribute: str = "", unit: str = "auto", site_layers: SiteLayers = None,
                         floors: Floors = None, limits: Limits = None) -> str:
        """Save a schedule (report and filters as in place_table) to a
        CSV that Excel opens as numbers (pt-BR: ';' and decimal comma). Without `file_path`, it goes next to
        the drawing as '<drawing> - <report>.csv'. Never overwrites without overwrite=True."""

        def fn() -> str:
            d = cad.doc()
            path = files.output_path(file_path or _beside_drawing(d, f"{report}.csv"), extensions={".csv"},
                                     overwrite=overwrite)
            schedule = reports.build(report, d, _filters(layers, handles, names, region, group_by_attribute, unit,
                                                         site_layers, floors, limits))
            rows = [*schedule.rows, *([schedule.footer] if schedule.footer else [])]
            files.write_csv(path, schedule.columns, rows)
            return f"CSV saved to {path} ({len(schedule.rows)} row(s))."

        return await acad.act("export_csv", {"report": report, "file_path": file_path, "layers": layers}, fn)
