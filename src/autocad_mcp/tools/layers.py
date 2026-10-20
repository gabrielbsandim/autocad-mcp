"""Layers: create, configure, rename, merge and standardize."""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files, layer_standard
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, READ, WRITE, spec
from autocad_mcp.tools.edit import color_index, ensure_linetype


def _layer(d: Any, name: str) -> Any:
    try:
        return d.Layers.Item(name)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"Layer {name!r} does not exist in this drawing.") from exc


def _apply_props(d: Any, lay: Any, color: Any = None, linetype: str = "", lineweight_mm: float | None = None,
                 on: bool | None = None, frozen: bool | None = None, locked: bool | None = None,
                 plottable: bool | None = None, description: str | None = None) -> None:
    if frozen and lay.Name.upper() == str(d.GetVariable("CLAYER")).upper():
        raise AcadError("The current layer cannot be frozen. Change the current layer first.")
    if color not in (None, ""):
        lay.Color = color_index(color)
    if linetype:
        ensure_linetype(d, linetype)
        lay.Linetype = linetype
    if lineweight_mm is not None:
        lay.Lineweight = acad.LW_DEFAULT if lineweight_mm < 0 else acad.lineweight_enum(lineweight_mm)
    if on is not None:
        lay.LayerOn = bool(on)
    if frozen is not None:
        lay.Freeze = bool(frozen)
    if locked is not None:
        lay.Lock = bool(locked)
    if plottable is not None:
        lay.Plottable = bool(plottable)
    if description is not None:
        lay.Description = description


def _move_all_to_layer(d: Any, source: str, target: str) -> int:
    """Move objects from `source` to `target` across the whole drawing (model, layouts and blocks).
    Any failure propagates, so the write wrapper rolls the whole merge back."""
    src = source.upper()
    moved = 0
    for b in d.Blocks:
        if b.IsXRef:
            continue
        for e in b:
            if str(e.Layer).upper() == src:
                e.Layer = target
                moved += 1
    return moved


def _merge_one(d: Any, source: str, target: str) -> str:
    lay = _layer(d, source)
    lay.Lock = False
    moved = _move_all_to_layer(d, source, target)
    if str(d.GetVariable("CLAYER")).upper() == source.upper():
        d.ActiveLayer = d.Layers.Item(target)
    try:
        lay.Delete()
    except Exception:  # noqa: BLE001
        return f"{source} -> {target}: {moved} object(s) moved; layer kept because something still references it"
    return f"{source} -> {target}: {moved} object(s) moved, layer deleted"


def _standard(standard: str) -> tuple[Any, list[layer_standard.LayerRule]]:
    path = layer_standard.built_in(standard) if standard else layer_standard.standard_path()
    if path is None:
        path = layer_standard.standard_path(files.safe_path(standard, extensions={".json"}, must_exist=True))
    try:
        return path, layer_standard.load_standard(path)
    except (OSError, ValueError, KeyError) as exc:
        raise AcadError(f"Could not read the layer standard {path}: {exc}") from exc


def _names(d: Any) -> set[str]:
    return {str(l.Name).upper() for l in d.Layers}


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Create or change layers", WRITE))
    async def manage_layer(action: Literal["create", "update", "set_current", "rename"], names: list[str],
                           new_name: str = "", color: str = "", linetype: str = "",
                           lineweight_mm: float | None = None, on: bool | None = None, frozen: bool | None = None,
                           locked: bool | None = None, plottable: bool | None = None,
                           description: str | None = None) -> str:
        """Layers, as one undo step. Only the parameters you pass change.
        - create: create `names` (or adjust them if they exist) with the given properties.
        - update: change properties of existing layers in `names` (turn off, freeze, lock, mark non-plotting...).
        - set_current: make names[0] the current layer (created and thawed if needed).
        - rename: rename names[0] to `new_name` (if `new_name` exists, use merge_layers).
        Color: a name ('red', 'gray'...) or an ACI number 1-255. Lineweight in mm (e.g. 0.25)."""
        if not names or not all(n.strip() for n in names):
            raise AcadError("Give at least one layer name.")

        def create(d: Any) -> str:
            for n in names:
                _apply_props(d, acad.ensure_layer(d, n), color or None, linetype, lineweight_mm, on, frozen, locked,
                             plottable, description)
            return f"{len(names)} layer(s) created or updated."

        def update(d: Any) -> str:
            layers = [_layer(d, n) for n in names]
            for lay in layers:
                _apply_props(d, lay, color or None, linetype, lineweight_mm, on, frozen, locked, plottable, description)
            return f"{len(layers)} layer(s) updated."

        def set_current(d: Any) -> str:
            lay = acad.ensure_layer(d, names[0])
            if lay.Freeze:
                lay.Freeze = False
            d.ActiveLayer = lay
            return f"Current layer: {names[0]}"

        def rename(d: Any) -> str:
            old = names[0]
            if old.upper() in layer_standard.PROTECTED:
                raise AcadError(f"Layer {old} is a system layer and cannot be renamed.")
            if not new_name.strip():
                raise AcadError("Pass new_name.")
            lay = _layer(d, old)
            if new_name.upper() != old.upper() and new_name.upper() in _names(d):
                raise AcadError(f"Layer {new_name} already exists. Use merge_layers to combine them.")
            lay.Name = new_name
            return f"Layer {old} renamed to {new_name}."

        actions = {"create": create, "update": update, "set_current": set_current, "rename": rename}
        args = {"action": action, "names": names, "new_name": new_name, "color": color, "linetype": linetype,
                "lineweight_mm": lineweight_mm, "on": on, "frozen": frozen, "locked": locked}
        return await acad.change("manage_layer", args, actions[action])

    @mcp.tool(**spec("Merge layers", DESTRUCTIVE))
    async def merge_layers(sources: list[str], target: str) -> str:
        """Merge layers: move every object (including inside blocks) from `sources` to `target` and delete the
        source layers. The target is created if it doesn't exist. Nothing changes if a source is missing."""
        wanted = [s for s in sources if s.upper() != target.upper() and s.upper() not in layer_standard.PROTECTED]
        if not wanted:
            raise AcadError("Nothing to merge: give source layers other than the target, 0 and Defpoints.")

        def body(d: Any) -> str:
            missing = [s for s in wanted if s.upper() not in _names(d)]
            if missing:
                raise AcadError(f"Layer(s) not found: {', '.join(missing)}. Nothing was merged.")
            acad.ensure_layer(d, target)
            return "Merge finished:\n" + "\n".join(_merge_one(d, s, target) for s in wanted)

        return await acad.change("merge_layers", {"sources": sources, "target": target}, body)

    @mcp.tool(**spec("Check layer standard", READ))
    async def check_layer_standard(standard: str = "") -> str:
        """Compare the drawing's layers with a layer standard and show the plan: what would be renamed,
        merged, what is already standard and what is not. Changes NOTHING. `standard`: 'asbea' (Brazilian,
        ARQ-ALV, ARQ-POR...), 'aia' (A-WALL, A-DOOR...) or the full path of an office JSON; empty = the one
        configured for this server."""

        def fn() -> str:
            path, rules = _standard(standard)
            d = cad.doc()
            names = [str(l.Name) for l in d.Layers]
            p = layer_standard.plan(names, rules)
            p["standard_used"] = str(path)
            present = {n.upper() for n in names}
            p["missing_standard_layers"] = [r.name for r in rules if r.name.upper() not in present]
            return acad.dumps(p)

        return await cad.run(fn)

    @mcp.tool(**spec("Apply layer standard", DESTRUCTIVE))
    async def apply_layer_standard(standard: str = "", create_missing: bool = False,
                                   apply_properties: bool = True) -> str:
        """Standardize the layers (run check_layer_standard first and show the plan to the user): renames,
        merges equivalent layers, applies the standard's color/linetype/lineweight/plot and, with
        create_missing=True, creates the missing standard layers. `standard` as in check_layer_standard.
        One undo step."""

        def body(d: Any) -> str:
            path, rules = _standard(standard)
            p = layer_standard.plan([str(l.Name) for l in d.Layers], rules)
            log = []
            for old, new in p["rename"]:
                d.Layers.Item(old).Name = new
                log.append(f"renamed {old} -> {new}")
            log.extend("merged " + _merge_one(d, old, new) for old, new in p["merge"])
            existing = {str(l.Name).upper(): l for l in d.Layers}
            for r in rules:
                lay = existing.get(r.name.upper())
                if lay is None and create_missing:
                    lay = d.Layers.Add(r.name)
                    log.append(f"created {r.name}")
                if lay is None or not apply_properties:
                    continue
                try:
                    _apply_props(d, lay, r.color, r.linetype, r.lineweight_mm, plottable=r.plot,
                                 description=r.description)
                except AcadError as exc:
                    log.append(f"warning on {r.name}: {exc}")
            if p["non_standard"]:
                log.append("non-standard (left untouched): " + ", ".join(p["non_standard"]))
            return f"Standardization with {path.name} finished:\n" + "\n".join(log)

        return await acad.change("apply_layer_standard", {"standard": standard,
                                                          "create_missing": create_missing}, body)
