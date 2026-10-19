"""Room areas: compute, label, build the area schedule (table) and export CSV."""
from __future__ import annotations

import csv
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.com import AcadError
from autocad_mcp.geometry import point_in_polygon, polygon_centroid
from autocad_mcp.tools import READ, WRITE
from autocad_mcp.tools.edit import resolve
from autocad_mcp.units import area_to_m2, fmt_num, length_to_m, meters_per_unit

POLY_TYPES = {"AcDbPolyline", "AcDb2dPolyline"}

UNIT_HELP = ("`unit`: drawing unit — 'auto' (reads INSUNITS), 'mm', 'cm' or 'm'. If the drawing "
             "declares no unit, ask the user.")

NO_NAME = "(unnamed)"


def _collect_rooms(d: Any, layers: list[str] | None, handles: list[str] | None, unit: str,
                   name_layers: list[str] | None) -> dict[str, Any]:
    """Find closed outlines and the name of each room (the text that sits inside it)."""
    factor, unit_desc = meters_per_unit(int(d.GetVariable("INSUNITS")), unit)
    if factor is None:
        raise AcadError(
            "The drawing declares no unit (INSUNITS=0). Ask the user whether it is in mm, cm or m and "
            "call again with unit='mm', 'cm' or 'm'."
        )

    if handles:
        polys = [e for e in resolve(d, handles) if e.ObjectName in POLY_TYPES]
        texts_pool = None
    else:
        lset = acad.normalize_layers(layers)
        polys, texts_pool = [], []
        name_lset = acad.normalize_layers(name_layers)
        for e in acad.iter_entities(d):
            try:
                t = e.ObjectName
                if t in POLY_TYPES and e.Closed and (not lset or e.Layer.upper() in lset):
                    polys.append(e)
                elif t in ("AcDbText", "AcDbMText") and (not name_lset or e.Layer.upper() in name_lset):
                    texts_pool.append(e)
            except Exception:  # noqa: BLE001
                continue

    if texts_pool is None:  # handles given: use text from the whole model
        texts_pool = [e for e in acad.iter_entities(d) if acad.matches(e, {"AcDbText", "AcDbMText"}, None)]
    texts = []
    for t in texts_pool:
        try:
            ip = t.InsertionPoint
            texts.append(((ip[0], ip[1]), acad.text_value(t)))
        except Exception:  # noqa: BLE001
            continue

    rooms = []
    for p in polys:
        try:
            pts = acad.lwpoly_points(p)
            area_u = float(p.Area)
            per_u = float(p.Length)
        except Exception:  # noqa: BLE001
            continue
        inside = [txt for (pt, txt) in texts if txt.strip() and point_in_polygon(pt, pts)]
        # Name = first text that doesn't look like an area value (e.g. "12.50 m²").
        name = next((t for t in inside if not _looks_like_area(t)), "")
        rooms.append({
            "handle": p.Handle,
            "room": name or NO_NAME,
            "texts_inside": inside[:5],
            "layer": p.Layer,
            "area_m2": round(area_to_m2(area_u, factor), 2),
            "perimeter_m": round(length_to_m(per_u, factor), 2),
            "center": [round(c, 4) for c in polygon_centroid(pts)],
        })
    rooms.sort(key=lambda r: r["room"].lower())
    total = round(sum(r["area_m2"] for r in rooms), 2)
    return {"drawing_unit": unit_desc, "factor_m": factor, "rooms": rooms, "total_m2": total}


def _looks_like_area(text: str) -> bool:
    t = text.lower().replace(" ", "")
    return "m²" in t or "m2" in t or t.replace(",", "").replace(".", "").isdigit()


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ, description=(
        "Compute the area (m²) and perimeter (m) of rooms. Uses CLOSED polylines: by default all of them in "
        "model space, or only those on the given `layers` (e.g. ['A-AREA']), or those in `handles` / ['selection']. "
        "Each room's name comes from the text inside its outline (restrict it with `name_layers`). "
        + UNIT_HELP))
    async def room_areas(layers: list[str] | None = None, handles: list[str] | None = None, unit: str = "auto",
                         name_layers: list[str] | None = None) -> str:
        def fn() -> str:
            data = _collect_rooms(com.doc(), layers, handles, unit, name_layers)
            for r in data["rooms"]:
                r.pop("center", None)
            data["total_formatted"] = f"{fmt_num(data['total_m2'])} m²"
            return acad.dumps(data)

        return await com.run(fn)

    @mcp.tool(annotations=WRITE, description=(
        "Write the area (e.g. 'A = 12.50 m²') at the center of each room, below its name. Same filters as "
        "room_areas. `text_height` in drawing units (default: 0.15 m converted). " + UNIT_HELP))
    async def label_room_areas(layers: list[str] | None = None, handles: list[str] | None = None,
                               unit: str = "auto", text_height: float = 0, layer: str = "A-ANNO-TEXT",
                               prefix: str = "A = ", include_name: bool = False) -> str:
        def body(d: Any) -> str:
            data = _collect_rooms(d, layers, handles, unit, None)
            h = text_height or 0.15 / data["factor_m"]
            try:
                d.Layers.Item(layer)
            except Exception:  # noqa: BLE001
                d.Layers.Add(layer)
            n = 0
            for r in data["rooms"]:
                cx, cy = r["center"]
                if include_name and r["room"] != NO_NAME:
                    t = d.ModelSpace.AddText(r["room"], acad.vpoint(cx, cy + h * 0.8), h)
                    _center(t, cx, cy + h * 0.8)
                    t.Layer = layer
                t = d.ModelSpace.AddText(f"{prefix}{fmt_num(r['area_m2'])} m²", acad.vpoint(cx, cy - h * 0.8), h * 0.8)
                _center(t, cx, cy - h * 0.8)
                t.Layer = layer
                n += 1
            return f"{n} area label(s) created on layer {layer}. Total: {fmt_num(data['total_m2'])} m²."

        return await com.run(acad.write("label_room_areas", {"layers": layers, "unit": unit}, body))

    @mcp.tool(annotations=WRITE, description=(
        "Create an AREA SCHEDULE (AutoCAD table) with room, area (m²) and perimeter (m), plus the total, "
        "at position [x,y] (top-left corner). Same filters as room_areas. `text_height` in drawing units "
        "(default 0.20 m converted). " + UNIT_HELP))
    async def create_area_table(position: list[float], layers: list[str] | None = None,
                                handles: list[str] | None = None, unit: str = "auto", title: str = "AREA SCHEDULE",
                                text_height: float = 0, include_perimeter: bool = True, layer: str = "A-ANNO-TEXT") -> str:
        def body(d: Any) -> str:
            data = _collect_rooms(d, layers, handles, unit, None)
            rooms = data["rooms"]
            if not rooms:
                raise AcadError("No closed outline found with these filters.")
            f = data["factor_m"]
            h = text_height or 0.20 / f
            headers = ["ROOM", "AREA (m²)"] + (["PERIMETER (m)"] if include_perimeter else [])
            ncols = len(headers)
            nrows = len(rooms) + 3  # title + header + total
            row_h = h * 2.2
            tbl = d.ModelSpace.AddTable(acad.vpoint(*position[:2]), nrows, ncols, row_h, h * 12)
            try:
                tbl.SetColumnWidth(0, h * 22)
            except Exception:  # noqa: BLE001
                pass
            for r in range(nrows):
                for c in range(ncols):
                    try:
                        tbl.SetCellTextHeight(r, c, h)
                    except Exception:  # noqa: BLE001
                        pass
            tbl.SetText(0, 0, title)
            for c, txt in enumerate(headers):
                tbl.SetText(1, c, txt)
            for i, room in enumerate(rooms, start=2):
                tbl.SetText(i, 0, room["room"])
                tbl.SetText(i, 1, fmt_num(room["area_m2"]))
                if include_perimeter:
                    tbl.SetText(i, 2, fmt_num(room["perimeter_m"]))
            last = nrows - 1
            tbl.SetText(last, 0, "TOTAL")
            tbl.SetText(last, 1, fmt_num(data["total_m2"]))
            try:
                d.Layers.Item(layer)
            except Exception:  # noqa: BLE001
                d.Layers.Add(layer)
            tbl.Layer = layer
            return (f"Area schedule created (handle {tbl.Handle}) with {len(rooms)} room(s). "
                    f"Total: {fmt_num(data['total_m2'])} m².")

        return await com.run(acad.write("create_area_table", {"position": position, "layers": layers}, body))

    @mcp.tool(annotations=WRITE, description=(
        "Export the area schedule to a CSV file. "
        "Without `file_path`, saves it next to the drawing as '<drawing> - areas.csv'. " + UNIT_HELP))
    async def export_areas_csv(file_path: str = "", layers: list[str] | None = None, handles: list[str] | None = None,
                               unit: str = "auto", overwrite: bool = False) -> str:
        def fn() -> str:
            d = com.doc()
            path = file_path or _beside_drawing(d, "areas.csv")
            check_csv_target(path, overwrite)
            data = _collect_rooms(d, layers, handles, unit, None)
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["Room", "Area (m²)", "Perimeter (m)", "Layer", "Handle"])
                for r in data["rooms"]:
                    w.writerow([r["room"], r["area_m2"], r["perimeter_m"], r["layer"], r["handle"]])
                w.writerow(["TOTAL", data["total_m2"], "", "", ""])
            acad.log_action("export_areas_csv", {"file_path": path}, f"{len(data['rooms'])} rooms")
            return f"CSV saved to {path} ({len(data['rooms'])} rooms, total {fmt_num(data['total_m2'])} m²)."

        return await com.run(fn)


def _center(text: Any, x: float, y: float) -> None:
    """Center a TEXT on the point (Middle Center alignment)."""
    try:
        text.Alignment = 10  # acAlignmentMiddleCenter
        text.TextAlignmentPoint = acad.vpoint(x, y)
    except Exception:  # noqa: BLE001
        pass


def check_csv_target(path: str, overwrite: bool) -> None:
    if os.path.splitext(path)[1].lower() != ".csv":
        raise AcadError("The output file must have the .csv extension.")
    if not os.path.isdir(os.path.dirname(os.path.abspath(path))):
        raise AcadError(f"Folder does not exist: {os.path.dirname(path)}")
    if os.path.exists(path) and not overwrite:
        raise AcadError(f"{path} already exists. Confirm with the user and use overwrite=True to replace it.")


def _beside_drawing(d: Any, suffix: str) -> str:
    full = d.FullName
    if full:
        base = os.path.splitext(full)[0]
        return f"{base} - {suffix}"
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs, f"{os.path.splitext(d.Name)[0]} - {suffix}")
