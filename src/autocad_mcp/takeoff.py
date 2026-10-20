"""Quantity takeoff: block counts (doors, windows, fixtures...) and lengths and hatch areas per layer."""
from __future__ import annotations

import fnmatch
from collections import Counter, defaultdict
from typing import Any

from autocad_mcp import acad
from autocad_mcp.units import area_to_m2, length_to_m, meters_per_unit

LINEAR = {"AcDbLine", "AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline", "AcDbArc", "AcDbCircle",
          "AcDbSpline", "AcDbEllipse"}


def attribute(ent: Any, tag: str) -> str:
    if not tag or not ent.HasAttributes:
        return ""
    for a in ent.GetAttributes():
        if str(a.TagString).upper() == tag.upper():
            return str(a.TextString)
    return ""


def count_blocks(document: Any, names: list[str] | None, layers: list[str] | None,
                 region: list[list[float]] | None, group_by_attribute: str) -> dict[str, Any]:
    lset = acad.normalize_layers(layers)
    pats = [n.upper() for n in names] if names else None
    counts: Counter[tuple[str, str]] = Counter()
    by_layer: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            if acad.kind(e) != "AcDbBlockReference":
                continue
            name = acad.effective_block_name(e)
            if pats and not any(fnmatch.fnmatchcase(name.upper(), p) for p in pats):
                continue
            if lset and str(e.Layer).upper() not in lset:
                continue
            if region and not acad.inside_region(e, region):
                continue
            key = (name, attribute(e, group_by_attribute))
            counts[key] += 1
            by_layer[key][str(e.Layer)] += 1
    rows = []
    for (name, attr), n in sorted(counts.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1])):
        row: dict[str, Any] = {"block": name, "count": n}
        if group_by_attribute:
            row[group_by_attribute.upper()] = attr
        row["layers"] = dict(by_layer[(name, attr)])
        rows.append(row)
    return scan.report({"items": rows, "total_blocks": sum(counts.values()), "complete": True})


def lengths_by_layer(document: Any, layers: list[str] | None, unit: str) -> dict[str, Any]:
    factor, desc = meters_per_unit(int(document.GetVariable("INSUNITS")), unit)
    lset = acad.normalize_layers(layers)
    lengths: Counter[str] = Counter()
    hatch_area: Counter[str] = Counter()
    approximate: set[str] = set()
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            layer = str(e.Layer)
            if lset and layer.upper() not in lset:
                continue
            t = acad.kind(e)
            if t in LINEAR:
                length, exact = acad.curve_length(e)
                lengths[layer] += length
                if not exact:
                    approximate.add(layer)
            elif t == "AcDbHatch":
                hatch_area[layer] += float(e.Area)
    out = []
    for lay in sorted(set(lengths) | set(hatch_area), key=str.lower):
        out.append({
            "layer": lay,
            "length_m": round(length_to_m(lengths[lay], factor), 2) if factor else None,
            "length_drawing_units": round(lengths[lay], 3),
            "hatch_area_m2": round(area_to_m2(hatch_area[lay], factor), 2) if factor and hatch_area[lay] else None,
        })
    res: dict[str, Any] = {"drawing_unit": desc, "by_layer": out, "complete": True}
    if approximate:
        res["approximate_layers"] = sorted(approximate)
        res["approximate_note"] = ("Splines on these layers were measured along their fit or control points, "
                                   "which is close to but not exactly their length.")
    if factor is None:
        res["unit_warning"] = "The drawing declares no unit: values in m were not computed. Pass unit='mm', 'cm' or 'm'."
    return scan.report(res)
