"""Pure layer-standard logic (no COM): load the standard and decide where each layer goes."""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_STANDARD = Path(__file__).parent / "standards" / "architecture_layers.json"

# System layers that must never be renamed.
PROTECTED = {"0", "DEFPOINTS"}


@dataclass
class LayerRule:
    name: str
    color: int = 7
    linetype: str = "Continuous"
    lineweight_mm: float | None = None
    description: str = ""
    plot: bool = True
    aliases: list[str] = field(default_factory=list)


def standard_path(custom: str = "") -> Path:
    if custom:
        return Path(custom)
    env = os.environ.get("AUTOCAD_MCP_LAYER_STANDARD", "")
    return Path(env) if env else DEFAULT_STANDARD


def load_standard(path: Path) -> list[LayerRule]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rules = []
    for item in data["layers"]:
        rules.append(LayerRule(
            name=item["name"],
            color=int(item.get("color", 7)),
            linetype=item.get("linetype", "Continuous"),
            lineweight_mm=item.get("lineweight_mm"),
            description=item.get("description", ""),
            plot=bool(item.get("plot", True)),
            aliases=list(item.get("aliases", [])),
        ))
    return rules


def _specificity(pattern: str) -> int:
    return sum(1 for c in pattern if c not in "*?")


def target_for(layer: str, rules: list[LayerRule]) -> LayerRule | None:
    """Standard rule for an existing layer. An exact name wins; otherwise the most specific alias."""
    up = layer.upper()
    if up in PROTECTED:
        return None
    for r in rules:
        if r.name.upper() == up:
            return r
    best: tuple[int, LayerRule] | None = None
    for r in rules:
        for a in r.aliases:
            if fnmatch.fnmatchcase(up, a.upper()):
                score = _specificity(a)
                if best is None or score > best[0]:
                    best = (score, r)
    return best[1] if best else None


def plan(existing: list[str], rules: list[LayerRule]) -> dict[str, list]:
    """Standardization plan: what to rename, what to merge (target already exists) and what is left out."""
    existing_up = {e.upper() for e in existing}
    renames: list[tuple[str, str]] = []
    merges: list[tuple[str, str]] = []
    ok: list[str] = []
    unknown: list[str] = []
    claimed: set[str] = set()
    for name in existing:
        r = target_for(name, rules)
        if r is None:
            if name.upper() not in PROTECTED:
                unknown.append(name)
            continue
        if r.name.upper() == name.upper():
            ok.append(name)
            continue
        if r.name.upper() in existing_up or r.name.upper() in claimed:
            merges.append((name, r.name))
        else:
            renames.append((name, r.name))
            claimed.add(r.name.upper())
    return {"rename": renames, "merge": merges, "already_standard": ok, "non_standard": unknown}
