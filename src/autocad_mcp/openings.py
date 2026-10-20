"""Openings schedule (quadro de esquadrias): doors and windows grouped by code, with their sizes."""
from __future__ import annotations

import fnmatch
import re
from collections import Counter
from typing import Any

from autocad_mcp import acad
from autocad_mcp.rooms import unit_factor
from autocad_mcp.takeoff import attribute

DOOR_NAMES = ["PORTA*", "DOOR*", "P[0-9]*", "PT[0-9]*"]
WINDOW_NAMES = ["JANELA*", "JAN[0-9]*", "WINDOW*", "J[0-9]*", "CAIXILHO*", "BASCULANTE*", "MAXIM*", "VITRO*"]
DOOR_LAYERS = ["ARQ-POR*", "A-DOOR*", "PORTA*", "DOOR*"]
WINDOW_LAYERS = ["ARQ-CXO*", "A-GLAZ*", "JANELA*", "CAIXILHO*", "WINDOW*", "ESQUADRIA*"]

CODE_TAGS = ["TIPO", "TYPE", "CODIGO", "CÓDIGO", "COD", "CODE", "REF", "MARCA", "TAG"]
WIDTH_TAGS = ["LARGURA", "LARG", "L", "WIDTH", "W"]
HEIGHT_TAGS = ["ALTURA", "ALT", "H", "HEIGHT"]
SILL_TAGS = ["PEITORIL", "PEIT", "SILL"]

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)")


def _matches(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value.upper(), p) for p in patterns)


def opening_kind(name: str, layer: str) -> str:
    if _matches(layer, DOOR_LAYERS) or _matches(name, DOOR_NAMES):
        return "door"
    if _matches(layer, WINDOW_LAYERS) or _matches(name, WINDOW_NAMES):
        return "window"
    return "opening"


def to_meters(raw: str) -> float | None:
    """'0,80' and '0.8' are meters, '80' centimeters and '800' millimeters: no opening is 10 m wide or 6 mm."""
    found = _NUMBER.search(raw or "")
    if not found:
        return None
    value = float(found.group().replace(",", "."))
    if value < 10:
        return value
    if value <= 600:
        return value / 100
    return value / 1000


def _first(attrs: dict[str, str], tags: list[str]) -> str:
    return next((attrs[t] for t in tags if attrs.get(t, "").strip()), "")


def _attributes(ent: Any) -> dict[str, str]:
    if not ent.HasAttributes:
        return {}
    return {str(a.TagString).upper(): str(a.TextString) for a in ent.GetAttributes()}


def _sizes(attrs: dict[str, str]) -> tuple[float | None, float | None, float | None]:
    width = to_meters(_first(attrs, WIDTH_TAGS))
    height = to_meters(_first(attrs, HEIGHT_TAGS))
    sill = to_meters(_first(attrs, SILL_TAGS))
    combined = next((m for v in attrs.values() if (m := _SIZE.search(v))), None)
    if combined and width is None:
        width = to_meters(combined.group(1))
    if combined and height is None:
        height = to_meters(combined.group(2))
    return width, height, sill


class _BlockWidths:
    """Local X extent of each block definition, measured once: a door's leaf or a window's span."""

    def __init__(self, document: Any) -> None:
        self.document = document
        self.cache: dict[str, float | None] = {}

    def __call__(self, name: str) -> float | None:
        if name not in self.cache:
            self.cache[name] = self._measure(name)
        return self.cache[name]

    def _measure(self, name: str) -> float | None:
        xs: list[float] = []
        for e in self.document.Blocks.Item(name):
            if acad.kind(e) == "AcDbAttributeDefinition":
                continue
            box = acad.bbox(e)
            if box:
                xs += [box["min"][0], box["max"][0]]
        return max(xs) - min(xs) if xs else None


def collect_openings(document: Any, names: list[str] | None, layers: list[str] | None,
                     region: list[list[float]] | None, code_attribute: str, unit: str) -> dict[str, Any]:
    factor, unit_desc = unit_factor(document, unit)
    lset = acad.normalize_layers(layers)
    pats = [n.upper() for n in names] if names else None
    widths = _BlockWidths(document)
    counts: Counter[tuple[str, str, float | None, float | None, float | None, str]] = Counter()
    scan = acad.Scan()
    for e in acad.iter_entities(document):
        with scan.item():
            if acad.kind(e) != "AcDbBlockReference":
                continue
            name = acad.effective_block_name(e)
            layer = str(e.Layer)
            if pats and not any(fnmatch.fnmatchcase(name.upper(), p) for p in pats):
                continue
            if lset and layer.upper() not in lset:
                continue
            kind = opening_kind(name, layer)
            if not pats and not lset and kind == "opening":
                continue
            if region and not acad.inside_region(e, region):
                continue
            attrs = _attributes(e)
            code = attribute(e, code_attribute) if code_attribute else _first(attrs, CODE_TAGS)
            width, height, sill = _sizes(attrs)
            source = "attribute"
            if width is None:
                local = widths(str(e.Name))
                width = None if local is None else local * abs(float(e.XScaleFactor)) * factor
                source = "measured"
            key = (kind, code or name, _r(width), _r(height), _r(sill), source)
            counts[key] += 1
    items = []
    for (kind, code, width, height, sill, source), n in sorted(counts.items(), key=_order):
        each = round(width * height, 2) if width and height else None
        items.append({"code": code, "kind": kind, "count": n, "width_m": width, "height_m": height,
                      "sill_m": sill, "width_from": source, "area_each_m2": each,
                      "area_total_m2": round(each * n, 2) if each else None})
    result: dict[str, Any] = {"drawing_unit": unit_desc, "factor_m": factor, "items": items,
                              "total_openings": sum(counts.values()), "complete": True}
    if any(i["height_m"] is None for i in items):
        result["note"] = ("Heights and sills only come from block attributes (ALTURA, PEITORIL, or a size like "
                          "'80x210'). Ask the user for the missing ones; widths marked 'measured' come from the "
                          "block drawing.")
    return scan.report(result)


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _order(kv: tuple[tuple[Any, ...], int]) -> tuple[int, list[Any]]:
    kind, code = kv[0][0], kv[0][1]
    natural = [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", code.upper())]
    return ["door", "window", "opening"].index(kind), natural
