"""Site areas for the building permit: lot, built area per floor, projection, permeable area, TO, CA and TP."""
from __future__ import annotations

from collections import Counter
from typing import Any

from autocad_mcp import acad
from autocad_mcp.errors import AcadError
from autocad_mcp.rooms import unit_factor
from autocad_mcp.units import area_to_m2

CATEGORIES = {
    "lot": ["TERRENO", "LOTE", "ARQ-___-TER", "ARQ-TER", "LIMITE DO TERRENO", "A-SITE", "A-PROP-LINE", "C-PROP"],
    "built": ["AREA CONSTRUIDA", "ÁREA CONSTRUÍDA", "AREA_CONSTRUIDA", "ARQ-___-ACO", "ARQ-ACO", "A-AREA-GROS"],
    "projection": ["PROJECAO", "PROJEÇÃO", "AREA DE PROJECAO", "ÁREA DE PROJEÇÃO", "ARQ-___-APJ", "A-AREA-PROJ"],
    "permeable": ["PERMEAVEL", "PERMEÁVEL", "AREA PERMEAVEL", "ÁREA PERMEÁVEL", "ARQ-___-PER", "A-AREA-PERM"],
}
REQUIRED = ("lot", "built")
NO_FLOOR = ""
UPPER_BOUND = {"TO": True, "CA": True, "TP": False}


def _resolve(document: Any, given: dict[str, list[str]]) -> dict[str, list[str]]:
    existing = {str(l.Name).upper(): str(l.Name) for l in document.Layers}
    chosen = {}
    for cat, names in CATEGORIES.items():
        wanted = given.get(cat) or [n for n in names if n.upper() in existing]
        chosen[cat] = [existing.get(n.upper(), n) for n in wanted]
    missing = [c for c in REQUIRED if not chosen[c]]
    if missing:
        raise AcadError(_ask_for_layers(document, missing))
    return chosen


def _ask_for_layers(document: Any, missing: list[str]) -> str:
    found: Counter[str] = Counter()
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            if _is_area(e):
                found[str(e.Layer)] += 1
    listing = ", ".join(f"{name} ({n})" for name, n in found.most_common(12)) or "none"
    return (f"Which layer holds the {' and the '.join(missing)} outline? Closed outlines and hatches found per "
            f"layer: {listing}. Call again with layers={{'lot': [...], 'built': [...], 'projection': [...], "
            "'permeable': [...]}}; projection and permeable are optional.")


def _is_area(ent: Any) -> bool:
    t = acad.kind(ent)
    return t == "AcDbHatch" or (t in acad.POLY_TYPES and bool(ent.Closed))


def _floor_of(ent: Any, floors: dict[str, list[list[float]]]) -> str:
    return next((name for name, region in floors.items() if acad.inside_region(ent, region)), NO_FLOOR)


def _fraction(value: float) -> float:
    """60 and 0.6 both mean 60 %: nobody sets an occupation limit of 0.6 %."""
    return value / 100 if value > 1 else value


def _checks(indices: dict[str, float | None], limits: dict[str, float]) -> dict[str, dict[str, Any]]:
    out = {}
    for key, raw in limits.items():
        name = key.strip().upper()
        value = indices.get(name)
        if name not in UPPER_BOUND or value is None:
            continue
        limit = raw if name == "CA" else _fraction(raw)
        ok = value <= limit if UPPER_BOUND[name] else value >= limit
        out[name] = {"value": value, "limit": round(limit, 4), "ok": ok}
    return out


def collect_site(document: Any, layers: dict[str, list[str]] | None, floors: dict[str, list[list[float]]] | None,
                 limits: dict[str, float] | None, unit: str) -> dict[str, Any]:
    factor, unit_desc = unit_factor(document, unit)
    chosen = _resolve(document, layers or {})
    by_layer = {n.upper(): cat for cat, names in chosen.items() for n in names}
    floors = floors or {}
    totals: Counter[str] = Counter()
    per_floor: Counter[str] = Counter()
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            cat = by_layer.get(str(e.Layer).upper())
            if cat is None or not _is_area(e):
                continue
            area = area_to_m2(float(e.Area), factor)
            totals[cat] += area
            if cat == "built":
                per_floor[_floor_of(e, floors)] += area
    lot = totals["lot"]
    if lot <= 0:
        raise AcadError(f"No closed outline found on the lot layer(s) {', '.join(chosen['lot'])}.")
    floor_rows = [{"floor": name, "built_m2": round(per_floor[name], 2)} for name in [*floors, NO_FLOOR]
                  if per_floor[name]]
    projection_estimated = not chosen["projection"]
    projection = max(per_floor.values(), default=0.0) if projection_estimated else totals["projection"]
    permeable = totals["permeable"] if chosen["permeable"] else None
    indices = {
        "TO": round(projection / lot, 4),
        "CA": round(totals["built"] / lot, 4),
        "TP": None if permeable is None else round(permeable / lot, 4),
    }
    result: dict[str, Any] = {
        "drawing_unit": unit_desc,
        "factor_m": factor,
        "layers_used": {cat: names for cat, names in chosen.items() if names},
        "lot_m2": round(lot, 2),
        "floors": floor_rows,
        "built_total_m2": round(totals["built"], 2),
        "projection_m2": round(projection, 2),
        "permeable_m2": None if permeable is None else round(permeable, 2),
        "indices": indices,
        "complete": True,
    }
    if projection_estimated:
        result["projection_note"] = ("No projection layer: the projection is the largest floor's built area. "
                                     "Tell the user, or call again with layers={'projection': [...]}.")
    if limits:
        result["checks"] = _checks(indices, limits)
    return scan.report(result)
