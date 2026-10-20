"""Every schedule the server can produce, as data that tools return, draw as a table or save as CSV."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autocad_mcp import openings, rooms, site, takeoff
from autocad_mcp.errors import AcadError
from autocad_mcp.units import locale

LABELS = {
    "pt-BR": {
        "rooms": "QUADRO DE ÁREAS", "room": "AMBIENTE", "number": "Nº", "area": "ÁREA (m²)",
        "perimeter": "PERÍMETRO (m)", "total": "TOTAL",
        "blocks": "QUANTITATIVO DE BLOCOS", "block": "BLOCO", "count": "QTD.",
        "lengths": "COMPRIMENTOS POR LAYER", "layer": "LAYER", "length": "COMPRIMENTO (m)",
        "hatch_area": "ÁREA DE HACHURA (m²)",
        "openings": "QUADRO DE ESQUADRIAS", "code": "CÓDIGO", "kind": "TIPO", "width": "LARGURA (m)",
        "height": "ALTURA (m)", "sill": "PEITORIL (m)", "area_each": "ÁREA UNIT. (m²)",
        "door": "PORTA", "window": "JANELA", "opening": "ESQUADRIA",
        "site": "QUADRO DE ÁREAS E ÍNDICES", "item": "DESCRIÇÃO", "value": "VALOR", "unit": "UNIDADE",
        "limit": "LIMITE", "status": "SITUAÇÃO", "lot": "ÁREA DO TERRENO", "built": "ÁREA CONSTRUÍDA",
        "built_total": "ÁREA CONSTRUÍDA TOTAL", "projection": "ÁREA DE PROJEÇÃO", "permeable": "ÁREA PERMEÁVEL",
        "TO": "TAXA DE OCUPAÇÃO (TO)", "CA": "COEFICIENTE DE APROVEITAMENTO (CA)",
        "TP": "TAXA DE PERMEABILIDADE (TP)", "no_floor": "SEM PAVIMENTO", "ok": "OK", "over": "ACIMA DO LIMITE",
        "under": "ABAIXO DO MÍNIMO",
    },
    "en-US": {
        "rooms": "AREA SCHEDULE", "room": "ROOM", "number": "No.", "area": "AREA (m²)",
        "perimeter": "PERIMETER (m)", "total": "TOTAL",
        "blocks": "QUANTITY TAKEOFF", "block": "BLOCK", "count": "COUNT",
        "lengths": "LENGTHS BY LAYER", "layer": "LAYER", "length": "LENGTH (m)", "hatch_area": "HATCH AREA (m²)",
        "openings": "DOOR AND WINDOW SCHEDULE", "code": "CODE", "kind": "KIND", "width": "WIDTH (m)",
        "height": "HEIGHT (m)", "sill": "SILL (m)", "area_each": "UNIT AREA (m²)",
        "door": "DOOR", "window": "WINDOW", "opening": "OPENING",
        "site": "SITE AREAS AND INDICES", "item": "ITEM", "value": "VALUE", "unit": "UNIT", "limit": "LIMIT",
        "status": "STATUS", "lot": "LOT AREA", "built": "BUILT AREA", "built_total": "TOTAL BUILT AREA",
        "projection": "FOOTPRINT AREA", "permeable": "PERMEABLE AREA", "TO": "LOT COVERAGE (TO)",
        "CA": "FLOOR AREA RATIO (CA)", "TP": "PERMEABILITY RATE (TP)", "no_floor": "NO FLOOR", "ok": "OK",
        "over": "OVER THE LIMIT", "under": "UNDER THE MINIMUM",
    },
}


def label(key: str) -> str:
    return LABELS[locale()][key]


@dataclass
class Filters:
    layers: list[str] | None = None
    handles: list[str] | None = None
    names: list[str] | None = None
    region: list[list[float]] | None = None
    group_by_attribute: str = ""
    unit: str = "auto"
    name_layers: list[str] | None = None
    site_layers: dict[str, list[str]] | None = None
    floors: dict[str, list[list[float]]] | None = None
    limits: dict[str, float] | None = None


@dataclass
class Schedule:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    footer: list[Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)


def rooms_schedule(document: Any, f: Filters) -> Schedule:
    data = rooms.collect_rooms(document, f.layers, f.handles, f.unit, f.name_layers)
    rows = [[r["room"], r["number"], r["area_m2"], r["perimeter_m"]] for r in data["rooms"]]
    columns = [label("room"), label("number"), label("area"), label("perimeter")]
    return Schedule(label("rooms"), columns, rows, [label("total"), "", data["total_m2"], ""], data)


def blocks_schedule(document: Any, f: Filters) -> Schedule:
    data = takeoff.count_blocks(document, f.names, f.layers, f.region, f.group_by_attribute)
    tag = f.group_by_attribute.upper()
    columns = [label("block")] + ([tag] if tag else []) + [label("count")]
    rows = [[i["block"]] + ([i[tag]] if tag else []) + [i["count"]] for i in data["items"]]
    footer = [label("total")] + ([""] if tag else []) + [data["total_blocks"]]
    return Schedule(label("blocks"), columns, rows, footer, data)


def lengths_schedule(document: Any, f: Filters) -> Schedule:
    data = takeoff.lengths_by_layer(document, f.layers, f.unit)
    rows = [[r["layer"], r["length_m"], r["hatch_area_m2"]] for r in data["by_layer"]]
    return Schedule(label("lengths"), [label("layer"), label("length"), label("hatch_area")], rows, None, data)


def openings_schedule(document: Any, f: Filters) -> Schedule:
    data = openings.collect_openings(document, f.names, f.layers, f.region, f.group_by_attribute, f.unit)
    columns = [label("code"), label("kind"), label("count"), label("width"), label("height"), label("sill"),
               label("area_each")]
    rows = [[i["code"], label(i["kind"]), i["count"], i["width_m"], i["height_m"], i["sill_m"], i["area_each_m2"]]
            for i in data["items"]]
    footer = [label("total"), "", data["total_openings"], "", "", "", ""]
    return Schedule(label("openings"), columns, rows, footer, data)


def _site_row(name: str, value: float | None, unit: str, check: dict[str, Any] | None, limits: bool) -> list[Any]:
    row: list[Any] = [name, value, unit]
    if not limits:
        return row
    if check is None:
        return [*row, "", ""]
    shown = check["limit"] * 100 if unit == "%" else check["limit"]
    status = "ok" if check["ok"] else ("over" if name in (label("TO"), label("CA")) else "under")
    return [*row, round(shown, 2), label(status)]


def site_schedule(document: Any, f: Filters) -> Schedule:
    data = site.collect_site(document, f.site_layers, f.floors, f.limits, f.unit)
    checks = data.get("checks", {})
    limits = bool(f.limits)
    rows = [_site_row(label("lot"), data["lot_m2"], "m²", None, limits)]
    for fl in data["floors"]:
        floor = fl["floor"] or (label("no_floor") if f.floors else "")
        rows.append(_site_row(f"{label('built')} {floor}".strip(), fl["built_m2"], "m²", None, limits))
    rows.append(_site_row(label("built_total"), data["built_total_m2"], "m²", None, limits))
    rows.append(_site_row(label("projection"), data["projection_m2"], "m²", None, limits))
    if data["permeable_m2"] is not None:
        rows.append(_site_row(label("permeable"), data["permeable_m2"], "m²", None, limits))
    for key, unit in (("TO", "%"), ("CA", ""), ("TP", "%")):
        value = data["indices"][key]
        if value is None:
            continue
        shown = round(value * 100, 2) if unit == "%" else round(value, 2)
        rows.append(_site_row(label(key), shown, unit, checks.get(key), limits))
    columns = [label("item"), label("value"), label("unit")] + ([label("limit"), label("status")] if limits else [])
    return Schedule(label("site"), columns, rows, None, data)


SCHEDULES = {
    "rooms": rooms_schedule,
    "blocks": blocks_schedule,
    "lengths": lengths_schedule,
    "openings": openings_schedule,
    "site": site_schedule,
}


def build(kind: str, document: Any, f: Filters) -> Schedule:
    maker = SCHEDULES.get(kind)
    if maker is None:
        raise AcadError(f"Unknown report {kind!r}. Use one of: {', '.join(SCHEDULES)}.")
    return maker(document, f)
