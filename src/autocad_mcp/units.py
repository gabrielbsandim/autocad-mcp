"""Drawing units (INSUNITS system variable) and number formatting."""
from __future__ import annotations

import os

# INSUNITS -> (name, how many meters one drawing unit is worth)
INSUNITS: dict[int, tuple[str, float | None]] = {
    0: ("unitless", None),
    1: ("inch", 0.0254),
    2: ("foot", 0.3048),
    4: ("millimeter", 0.001),
    5: ("centimeter", 0.01),
    6: ("meter", 1.0),
    14: ("decimeter", 0.1),
}

UNIT_ALIASES: dict[str, float] = {
    "mm": 0.001,
    "cm": 0.01,
    "dm": 0.1,
    "m": 1.0,
}

LOCALES = {
    "pt-BR": {"thousands": ".", "decimal": ",", "csv_delimiter": ";"},
    "en-US": {"thousands": ",", "decimal": ".", "csv_delimiter": ","},
}


def locale() -> str:
    value = os.environ.get("AUTOCAD_MCP_LOCALE", "pt-BR").strip()
    return value if value in LOCALES else "pt-BR"


def meters_per_unit(insunits: int, override: str = "auto") -> tuple[float | None, str]:
    """Conversion factor from drawing unit to meter.

    `override` accepts "auto", "mm", "cm", "dm" or "m". Returns (factor, description);
    a factor of None means the drawing declares no unit and none was given.
    """
    key = (override or "auto").strip().lower()
    if key != "auto":
        if key not in UNIT_ALIASES:
            raise ValueError(f"Invalid unit {override!r}. Use auto, mm, cm, dm or m.")
        return UNIT_ALIASES[key], f"{key} (given)"
    name, factor = INSUNITS.get(insunits, (f"code {insunits}", None))
    return factor, name


def area_to_m2(area_units: float, factor: float | None) -> float | None:
    return None if factor is None else area_units * factor * factor


def length_to_m(length_units: float, factor: float | None) -> float | None:
    return None if factor is None else length_units * factor


def fmt_num(value: float | None, decimals: int = 2, loc: str = "") -> str:
    """1234.5 -> '1.234,50' in pt-BR, '1,234.50' in en-US."""
    if value is None:
        return "-"
    sep = LOCALES[loc or locale()]
    raw = f"{value:,.{decimals}f}"
    return raw.replace(",", "\0").replace(".", sep["decimal"]).replace("\0", sep["thousands"])


def csv_delimiter(loc: str = "") -> str:
    return LOCALES[loc or locale()]["csv_delimiter"]
