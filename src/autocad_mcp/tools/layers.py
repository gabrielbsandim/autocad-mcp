"""Layers: list, create, configure, rename, merge and standardize."""
from __future__ import annotations

from collections import Counter
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files, layer_standard
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, READ, WRITE
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


def _count_by_layer(d: Any, scan: acad.Scan) -> Counter[str]:
    c: Counter[str] = Counter()
    for b in d.Blocks:
        if b.IsXRef:
            continue
        for e in b:
            with scan.item():
                c[str(e.Layer).upper()] += 1
    return c


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


def _standard(standard_file: str) -> tuple[Any, list[layer_standard.LayerRule]]:
    path = layer_standard.standard_path(files.safe_path(standard_file, extensions={".json"}, must_exist=True)
                                        if standard_file else "")
    try:
        return path, layer_standard.load_standard(path)
    except (OSError, ValueError, KeyError) as exc:
        raise AcadError(f"Could not read the layer standard {path}: {exc}") from exc


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def list_layers(count_objects: bool = True) -> str:
        """List layers with color, linetype, lineweight, on/frozen/locked/plot state and
        how many objects each one has (including inside blocks)."""

        def fn() -> str:
            d = cad.doc()
            scan = acad.Scan()
            counts = _count_by_layer(d, scan) if count_objects else Counter()
            current = str(d.GetVariable("CLAYER")).upper()
            rows = []
            for lay in d.Layers:
                lw = lay.Lineweight
                rows.append({
                    "name": lay.Name,
                    "current": lay.Name.upper() == current,
                    "color": lay.Color,
                    "linetype": lay.Linetype,
                    "lineweight_mm": (lw / 100) if lw >= 0 else "default",
                    "on": bool(lay.LayerOn),
                    "frozen": bool(lay.Freeze),
                    "locked": bool(lay.Lock),
                    "plot": bool(lay.Plottable),
                    "description": lay.Description,
                    "objects": counts.get(lay.Name.upper(), 0) if count_objects else None,
                })
            return acad.dumps(scan.report({"layers": rows}))

        return await cad.run(fn)

    @mcp.tool(annotations=WRITE)
    async def create_layer(name: str, color: str = "", linetype: str = "", lineweight_mm: float | None = None,
                           plottable: bool = True, description: str = "", make_current: bool = False) -> str:
        """Create a layer (or adjust it if it already exists). Color: a name ('red', 'blue',
        'gray'...) or an ACI number 1-255. Lineweight in mm (e.g. 0.25)."""
        if not name.strip():
            raise AcadError("The layer name cannot be empty.")

        def body(d: Any) -> str:
            existed = name.upper() in {str(l.Name).upper() for l in d.Layers}
            lay = acad.ensure_layer(d, name)
            _apply_props(d, lay, color or None, linetype, lineweight_mm, plottable=plottable,
                         description=description or None)
            if make_current:
                d.ActiveLayer = lay
            return f"Layer {name} {'updated' if existed else 'created'}{' and made current' if make_current else ''}."

        return await acad.change("create_layer", {"name": name, "color": color, "linetype": linetype,
                                                  "make_current": make_current}, body)

    @mcp.tool(annotations=WRITE)
    async def set_layer_properties(names: list[str], color: str = "", linetype: str = "",
                                   lineweight_mm: float | None = None, on: bool | None = None,
                                   frozen: bool | None = None, locked: bool | None = None,
                                   plottable: bool | None = None, description: str | None = None) -> str:
        """Change properties of one or more layers (only the given parameters change).
        E.g. turn off furniture layers, lock the wall layer, mark a layer as non-plotting."""

        def body(d: Any) -> str:
            layers = [_layer(d, n) for n in names]
            for lay in layers:
                _apply_props(d, lay, color or None, linetype, lineweight_mm, on, frozen, locked,
                             plottable, description)
            return f"{len(layers)} layer(s) updated."

        return await acad.change("set_layer_properties", {"names": names, "color": color, "on": on,
                                                          "frozen": frozen, "locked": locked}, body)

    @mcp.tool(annotations=WRITE)
    async def set_current_layer(name: str) -> str:
        """Set the current layer (where the user will draw next). Creates it if it doesn't exist."""

        def body(d: Any) -> str:
            lay = acad.ensure_layer(d, name)
            if lay.Freeze:
                lay.Freeze = False
            d.ActiveLayer = lay
            return f"Current layer: {name}"

        return await acad.change("set_current_layer", {"name": name}, body)

    @mcp.tool(annotations=WRITE)
    async def rename_layer(old_name: str, new_name: str) -> str:
        """Rename a layer. If a layer with the new name already exists, use merge_layers."""
        if old_name.upper() in layer_standard.PROTECTED:
            raise AcadError(f"Layer {old_name} is a system layer and cannot be renamed.")
        if not new_name.strip():
            raise AcadError("The new name cannot be empty.")

        def body(d: Any) -> str:
            lay = _layer(d, old_name)
            taken = {str(l.Name).upper() for l in d.Layers}
            if new_name.upper() != old_name.upper() and new_name.upper() in taken:
                raise AcadError(f"Layer {new_name} already exists. Use merge_layers to combine them.")
            lay.Name = new_name
            return f"Layer {old_name} renamed to {new_name}."

        return await acad.change("rename_layer", {"old": old_name, "new": new_name}, body)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def merge_layers(sources: list[str], target: str) -> str:
        """Merge layers: move every object (including inside blocks) from `sources` to
        `target` and delete the source layers. The target is created if it doesn't exist."""
        wanted = [s for s in sources if s.upper() != target.upper() and s.upper() not in layer_standard.PROTECTED]
        if not wanted:
            raise AcadError("Nothing to merge: give source layers other than the target, 0 and Defpoints.")

        def body(d: Any) -> str:
            missing = [s for s in wanted if s.upper() not in {str(l.Name).upper() for l in d.Layers}]
            if missing:
                raise AcadError(f"Layer(s) not found: {', '.join(missing)}. Nothing was merged.")
            acad.ensure_layer(d, target)
            return "Merge finished:\n" + "\n".join(_merge_one(d, s, target) for s in wanted)

        return await acad.change("merge_layers", {"sources": sources, "target": target}, body)

    @mcp.tool(annotations=READ)
    async def check_layer_standard(standard_file: str = "") -> str:
        """Compare the drawing's layers with the office standard (JSON file) and show the
        plan: what would be renamed, merged, what is already standard and what is not.
        Changes NOTHING. Without `standard_file` it uses the built-in architecture standard
        (or the one set in AUTOCAD_MCP_LAYER_STANDARD)."""

        def fn() -> str:
            path, rules = _standard(standard_file)
            d = cad.doc()
            names = [str(l.Name) for l in d.Layers]
            p = layer_standard.plan(names, rules)
            p["standard_used"] = str(path)
            present = {n.upper() for n in names}
            p["missing_standard_layers"] = [r.name for r in rules if r.name.upper() not in present]
            return acad.dumps(p)

        return await cad.run(fn)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def apply_layer_standard(standard_file: str = "", create_missing: bool = False,
                                   apply_properties: bool = True) -> str:
        """Standardize the drawing's layers according to the standard (run check_layer_standard first
        and show the plan to the user). Renames, merges equivalent layers, applies the standard's
        color/linetype/lineweight/plot settings and, with create_missing=True, creates the missing
        standard layers. Everything becomes a single undo step."""

        def body(d: Any) -> str:
            path, rules = _standard(standard_file)
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

        return await acad.change("apply_layer_standard", {"standard": standard_file,
                                                          "create_missing": create_missing}, body)
