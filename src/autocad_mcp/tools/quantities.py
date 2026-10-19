"""Quantity takeoff: block counts (doors, windows, fixtures...) and lengths per layer."""
from __future__ import annotations

import csv
import fnmatch
from collections import Counter, defaultdict
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.tools import READ, WRITE
from autocad_mcp.tools.areas import UNIT_HELP, _beside_drawing, check_csv_target
from autocad_mcp.tools.query import _in_region
from autocad_mcp.units import area_to_m2, fmt_num, length_to_m, meters_per_unit

LINEAR = {"AcDbLine", "AcDbPolyline", "AcDb2dPolyline", "AcDbArc", "AcDbCircle", "AcDbSpline"}


def _length(e: Any) -> float:
    t = e.ObjectName
    if t == "AcDbArc":
        return float(e.ArcLength)
    if t == "AcDbCircle":
        return float(e.Circumference)
    return float(e.Length)


def _count_blocks(d: Any, names: list[str] | None, layers: list[str] | None, region: list[list[float]] | None,
                  group_by_attribute: str) -> dict[str, Any]:
    lset = acad.normalize_layers(layers)
    pats = [n.upper() for n in names] if names else None
    counts: Counter[tuple[str, str]] = Counter()
    by_layer: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for e in acad.iter_entities(d):
        try:
            if e.ObjectName != "AcDbBlockReference":
                continue
            name = acad.effective_block_name(e)
            if pats and not any(fnmatch.fnmatchcase(name.upper(), p) for p in pats):
                continue
            if lset and e.Layer.upper() not in lset:
                continue
            if region and not _in_region(e, region):
                continue
            key_attr = ""
            if group_by_attribute and e.HasAttributes:
                for a in e.GetAttributes():
                    if a.TagString.upper() == group_by_attribute.upper():
                        key_attr = a.TextString
                        break
            counts[(name, key_attr)] += 1
            by_layer[(name, key_attr)][e.Layer] += 1
        except Exception:  # noqa: BLE001
            continue
    rows = []
    for (name, attr), n in sorted(counts.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1])):
        row: dict[str, Any] = {"block": name, "count": n}
        if group_by_attribute:
            row[group_by_attribute.upper()] = attr
        row["layers"] = dict(by_layer[(name, attr)])
        rows.append(row)
    return {"items": rows, "total_blocks": sum(counts.values())}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def count_blocks(names: list[str] | None = None, layers: list[str] | None = None,
                           region: list[list[float]] | None = None, group_by_attribute: str = "") -> str:
        """Block quantity takeoff (doors, windows, fixtures, lights, furniture...).
        - names: filter by name, wildcards allowed (e.g. ["DOOR*", "WINDOW*"]).
        - layers: only blocks on these layers. region: [[x1,y1],[x2,y2]] to count only part of the drawing (e.g. one floor).
        - group_by_attribute: split the count by the value of an attribute (e.g. "TYPE" -> D1, D2, W1...)."""

        def fn() -> str:
            return acad.dumps(_count_blocks(com.doc(), names, layers, region, group_by_attribute))

        return await com.run(fn)

    @mcp.tool(annotations=READ, description=(
        "Lengths per layer: sums the length (m) of lines, polylines, arcs and circles and the area (m²) "
        "of hatches. Useful for baseboards, thresholds, walls (centerline length), ceilings, hatched floors. "
        "Filter with `layers`. " + UNIT_HELP))
    async def lengths_by_layer(layers: list[str] | None = None, unit: str = "auto") -> str:
        def fn() -> str:
            d = com.doc()
            factor, desc = meters_per_unit(int(d.GetVariable("INSUNITS")), unit)
            lset = acad.normalize_layers(layers)
            lengths: Counter[str] = Counter()
            hatch_area: Counter[str] = Counter()
            for e in acad.iter_entities(d):
                try:
                    if lset and e.Layer.upper() not in lset:
                        continue
                    t = e.ObjectName
                    if t in LINEAR:
                        lengths[e.Layer] += _length(e)
                    elif t == "AcDbHatch":
                        hatch_area[e.Layer] += float(e.Area)
                except Exception:  # noqa: BLE001
                    continue
            out = []
            for lay in sorted(set(lengths) | set(hatch_area), key=str.lower):
                out.append({
                    "layer": lay,
                    "length_m": round(length_to_m(lengths[lay], factor), 2) if factor else None,
                    "length_drawing_units": round(lengths[lay], 3),
                    "hatch_area_m2": round(area_to_m2(hatch_area[lay], factor), 2) if factor and hatch_area[lay] else None,
                })
            res: dict[str, Any] = {"drawing_unit": desc, "by_layer": out}
            if factor is None:
                res["warning"] = "The drawing declares no unit: values in m were not computed. Pass unit='mm', 'cm' or 'm'."
            return acad.dumps(res)

        return await com.run(fn)

    @mcp.tool(annotations=WRITE)
    async def export_quantities_csv(file_path: str = "", names: list[str] | None = None,
                                    layers: list[str] | None = None, group_by_attribute: str = "",
                                    overwrite: bool = False) -> str:
        """Export the block quantity takeoff to CSV.
        Without `file_path`, saves it next to the drawing as '<drawing> - quantities.csv'."""

        def fn() -> str:
            d = com.doc()
            path = file_path or _beside_drawing(d, "quantities.csv")
            check_csv_target(path, overwrite)
            data = _count_blocks(d, names, layers, None, group_by_attribute)
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                header = ["Block"] + ([group_by_attribute.upper()] if group_by_attribute else []) + ["Count", "Layers"]
                w.writerow(header)
                for r in data["items"]:
                    row = [r["block"]]
                    if group_by_attribute:
                        row.append(r[group_by_attribute.upper()])
                    row += [r["count"], ", ".join(f"{k} ({v})" for k, v in r["layers"].items())]
                    w.writerow(row)
                w.writerow(["TOTAL"] + ([""] if group_by_attribute else []) + [data["total_blocks"], ""])
            acad.log_action("export_quantities_csv", {"file_path": path}, f"{len(data['items'])} rows")
            return f"Quantity takeoff saved to {path} ({len(data['items'])} type(s), {fmt_num(data['total_blocks'], 0)} block(s))."

        return await com.run(fn)
