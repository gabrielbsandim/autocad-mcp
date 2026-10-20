"""Every schedule the server can produce, as data that tools return, draw as a table or save as CSV."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autocad_mcp import rooms, takeoff
from autocad_mcp.errors import AcadError


@dataclass
class Filters:
    layers: list[str] | None = None
    handles: list[str] | None = None
    names: list[str] | None = None
    region: list[list[float]] | None = None
    group_by_attribute: str = ""
    unit: str = "auto"
    name_layers: list[str] | None = None


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
    return Schedule("AREA SCHEDULE", ["ROOM", "No.", "AREA (m²)", "PERIMETER (m)"], rows,
                    ["TOTAL", "", data["total_m2"], ""], data)


def blocks_schedule(document: Any, f: Filters) -> Schedule:
    data = takeoff.count_blocks(document, f.names, f.layers, f.region, f.group_by_attribute)
    tag = f.group_by_attribute.upper()
    columns = ["BLOCK"] + ([tag] if tag else []) + ["COUNT"]
    rows = [[i["block"]] + ([i[tag]] if tag else []) + [i["count"]] for i in data["items"]]
    footer = ["TOTAL"] + ([""] if tag else []) + [data["total_blocks"]]
    return Schedule("QUANTITY TAKEOFF", columns, rows, footer, data)


def lengths_schedule(document: Any, f: Filters) -> Schedule:
    data = takeoff.lengths_by_layer(document, f.layers, f.unit)
    rows = [[r["layer"], r["length_m"], r["hatch_area_m2"]] for r in data["by_layer"]]
    return Schedule("LENGTHS BY LAYER", ["LAYER", "LENGTH (m)", "HATCH AREA (m²)"], rows, None, data)


SCHEDULES = {
    "rooms": rooms_schedule,
    "blocks": blocks_schedule,
    "lengths": lengths_schedule,
}


def build(kind: str, document: Any, f: Filters) -> Schedule:
    maker = SCHEDULES.get(kind)
    if maker is None:
        raise AcadError(f"Unknown report {kind!r}. Use one of: {', '.join(SCHEDULES)}.")
    return maker(document, f)
