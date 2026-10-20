"""Room areas: find the room outlines, name each one from the text inside it, total them."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from autocad_mcp import acad
from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import interior_point, point_in_polygon
from autocad_mcp.units import area_to_m2, length_to_m, meters_per_unit

ROOM_LAYER_NAMES = ["A-AREA", "A-AREA-IDEN", "ARQ-___-ARE", "ARQ-ARE", "AREA", "AREAS", "ÁREA", "ÁREAS",
                    "AMBIENTES", "ROOMS"]

NO_NAME = "(unnamed)"

_DECIMAL = re.compile(r"^\d{1,3}([.,]\d{3})*[.,]\d+$")
_INTEGER = re.compile(r"^\d+[a-z]?$")


def looks_like_area(text: str) -> bool:
    """'12,50 m²', 'A=12.5' and '12,50' are area labels; '101' or '12A' are room numbers."""
    t = text.lower().replace(" ", "")
    if "m²" in t or "m2" in t:
        return True
    if t.startswith(("a=", "área=", "area=", "a:")):
        return True
    return bool(_DECIMAL.match(t))


def pick_name(texts: list[str]) -> tuple[str, str]:
    """(name, number) from the texts inside a room, ignoring area labels."""
    candidates = [t.strip() for t in texts if t.strip() and not looks_like_area(t)]
    number = next((t for t in candidates if _INTEGER.match(t.lower())), "")
    name = next((t for t in candidates if not _INTEGER.match(t.lower())), "")
    return name or number or NO_NAME, number


def unit_factor(document: Any, unit: str) -> tuple[float, str]:
    try:
        factor, desc = meters_per_unit(int(document.GetVariable("INSUNITS")), unit)
    except ValueError as exc:
        raise AcadError(str(exc)) from exc
    if factor is None:
        raise AcadError(
            "The drawing declares no unit (INSUNITS=0). Ask the user whether it is in mm, cm or m and "
            "call again with unit='mm', 'cm' or 'm'."
        )
    return factor, desc


def _closed_outline_layers(document: Any) -> Counter[str]:
    found: Counter[str] = Counter()
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            if acad.kind(e) in acad.POLY_TYPES and e.Closed:
                found[str(e.Layer)] += 1
    return found


def room_layers(document: Any, layers: list[str] | None) -> list[str]:
    """The layers holding room outlines. Without a choice, only well-known room layers count,
    because walls and furniture are closed polylines too and would inflate the total."""
    if layers:
        return layers
    existing = {str(l.Name).upper(): str(l.Name) for l in document.Layers}
    known = [existing[n.upper()] for n in ROOM_LAYER_NAMES if n.upper() in existing]
    if known:
        return known
    candidates = _closed_outline_layers(document)
    if not candidates:
        raise AcadError("No closed outline (closed polyline) was found in model space.")
    listing = ", ".join(f"{name} ({n})" for name, n in candidates.most_common(10))
    raise AcadError(
        "Which layer holds the room outlines? Walls and furniture are closed outlines too, so I need the "
        f"layer to avoid counting them. Closed outlines found per layer: {listing}. "
        "Call again with layers=[...]."
    )


def collect_rooms(document: Any, layers: list[str] | None, handles: list[str] | None, unit: str,
                  name_layers: list[str] | None) -> dict[str, Any]:
    factor, unit_desc = unit_factor(document, unit)
    scan = acad.Scan()
    name_lset = acad.normalize_layers(name_layers)
    polys: list[Any] = []
    texts: list[tuple[tuple[float, float], str, str]] = []
    used_layers: list[str] = []
    if handles:
        polys = [e for e in acad.resolve_handles(document, handles)
                 if acad.kind(e) in acad.POLY_TYPES and e.Closed]
    else:
        used_layers = room_layers(document, layers)
    lset = acad.normalize_layers(used_layers)
    for e in acad.iter_entities(document):
        with scan.item():
            t = acad.kind(e)
            layer = str(e.Layer).upper()
            if not handles and t in acad.POLY_TYPES and layer in lset and e.Closed:
                polys.append(e)
            elif t in acad.TEXT_TYPES and (not name_lset or layer in name_lset):
                ip = e.InsertionPoint
                texts.append(((float(ip[0]), float(ip[1])), acad.text_value(e), layer))

    rooms = []
    outlines = []
    for p in polys:
        with scan.item():
            pts = acad.lwpoly_points(p)
            area_u = float(p.Area)
            per_u = float(p.Length)
            inside = [txt for (pt, txt, _) in texts if txt.strip() and point_in_polygon(pt, pts)]
            name, number = pick_name(inside)
            rooms.append({
                "handle": p.Handle,
                "room": name,
                "number": number,
                "texts_inside": inside[:5],
                "layer": p.Layer,
                "area_m2": round(area_to_m2(area_u, factor), 2),
                "perimeter_m": round(length_to_m(per_u, factor), 2),
                "label_point": [round(c, 4) for c in interior_point(pts)],
            })
            outlines.append((p.Handle, pts))
    rooms.sort(key=lambda r: (r["room"].lower(), r["number"]))
    result: dict[str, Any] = {
        "drawing_unit": unit_desc,
        "factor_m": factor,
        "layers_used": used_layers or None,
        "rooms": rooms,
        "total_m2": round(sum(r["area_m2"] for r in rooms), 2),
        "complete": True,
    }
    overlaps = _overlaps(outlines)
    if overlaps:
        result["overlapping_outlines"] = overlaps
        result["overlap_warning"] = ("Some outlines sit inside others, so their area is counted twice in the "
                                     "total. Ask the user whether those are separate rooms.")
    if unnamed := sum(1 for r in rooms if r["room"] == NO_NAME):
        result["unnamed_rooms"] = unnamed
    return scan.report(result)


def _overlaps(outlines: list[tuple[str, list[tuple[float, float]]]]) -> list[list[str]]:
    pairs = []
    for handle, pts in outlines:
        for other, other_pts in outlines:
            if other != handle and pts and point_in_polygon(interior_point(pts), other_pts):
                pairs.append([handle, other])
    return pairs[:20]
