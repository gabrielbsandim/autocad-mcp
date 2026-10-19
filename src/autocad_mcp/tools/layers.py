"""Layers: list, create, configure, rename, merge and standardize."""
from __future__ import annotations

from collections import Counter
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com, layer_standard
from autocad_mcp.com import AcadError
from autocad_mcp.tools import DESTRUCTIVE, READ, WRITE
from autocad_mcp.tools.edit import _ensure_linetype, color_index

VALID_LW = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]


def lineweight_enum(mm: float) -> int:
    """0.25 mm -> 25 (closest valid AutoCAD value)."""
    if mm < 0:
        return -3  # default
    target = mm * 100
    return min(VALID_LW, key=lambda v: abs(v - target))


def _layer(d: Any, name: str) -> Any:
    try:
        return d.Layers.Item(name)
    except Exception as exc:  # noqa: BLE001
        raise AcadError(f"Layer {name!r} does not exist in this drawing.") from exc


def _apply_props(d: Any, lay: Any, color: Any = None, linetype: str = "", lineweight_mm: float | None = None,
                 on: bool | None = None, frozen: bool | None = None, locked: bool | None = None,
                 plottable: bool | None = None, description: str | None = None) -> None:
    if color not in (None, ""):
        lay.Color = color_index(color)
    if linetype:
        _ensure_linetype(d, linetype)
        lay.Linetype = linetype
    if lineweight_mm is not None:
        lay.Lineweight = lineweight_enum(lineweight_mm)
    if on is not None:
        lay.LayerOn = bool(on)
    if frozen is not None:
        if frozen and lay.Name.upper() == str(d.GetVariable("CLAYER")).upper():
            raise AcadError("The current layer cannot be frozen. Change the current layer first.")
        lay.Freeze = bool(frozen)
    if locked is not None:
        lay.Lock = bool(locked)
    if plottable is not None:
        lay.Plottable = bool(plottable)
    if description is not None:
        lay.Description = description


def _count_by_layer(d: Any) -> Counter[str]:
    c: Counter[str] = Counter()
    for b in d.Blocks:
        try:
            if b.IsXRef:
                continue
            for e in b:
                try:
                    c[e.Layer.upper()] += 1
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return c


def _move_all_to_layer(d: Any, source: str, target: str) -> int:
    """Move objects from `source` to `target` across the whole drawing (model, layouts and blocks)."""
    src = source.upper()
    moved = 0
    for b in d.Blocks:
        try:
            if b.IsXRef:
                continue
            for e in b:
                try:
                    if e.Layer.upper() == src:
                        e.Layer = target
                        moved += 1
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return moved


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def list_layers(count_objects: bool = True) -> str:
        """List layers with color, linetype, lineweight, on/frozen/locked/plot state and
        how many objects each one has (including inside blocks)."""

        def fn() -> str:
            d = com.doc()
            counts = _count_by_layer(d) if count_objects else Counter()
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
            return acad.dumps(rows)

        return await com.run(fn)

    @mcp.tool(annotations=WRITE)
    async def create_layer(name: str, color: str = "", linetype: str = "", lineweight_mm: float | None = None,
                           plottable: bool = True, description: str = "", make_current: bool = False) -> str:
        """Create a layer (or adjust it if it already exists). Color: a name ('red', 'blue',
        'gray'...) or an ACI number 1–255. Lineweight in mm (e.g. 0.25)."""

        def body(d: Any) -> str:
            try:
                lay = d.Layers.Item(name)
                existed = True
            except Exception:  # noqa: BLE001
                lay = d.Layers.Add(name)
                existed = False
            _apply_props(d, lay, color or None, linetype, lineweight_mm, plottable=plottable,
                         description=description or None)
            if make_current:
                d.ActiveLayer = lay
            return f"Layer {name} {'updated' if existed else 'created'}{' and made current' if make_current else ''}."

        return await com.run(acad.write("create_layer", {"name": name, "color": color}, body))

    @mcp.tool(annotations=WRITE)
    async def set_layer_properties(names: list[str], color: str = "", linetype: str = "",
                                   lineweight_mm: float | None = None, on: bool | None = None,
                                   frozen: bool | None = None, locked: bool | None = None,
                                   plottable: bool | None = None, description: str | None = None) -> str:
        """Change properties of one or more layers (only the given parameters change).
        E.g. turn off furniture layers, lock the wall layer, mark a layer as non-plotting."""

        def body(d: Any) -> str:
            for n in names:
                _apply_props(d, _layer(d, n), color or None, linetype, lineweight_mm, on, frozen, locked,
                             plottable, description)
            return f"{len(names)} layer(s) updated."

        return await com.run(acad.write("set_layer_properties", {"names": names}, body))

    @mcp.tool(annotations=WRITE)
    async def set_current_layer(name: str) -> str:
        """Set the current layer (where the user will draw next). Creates it if it doesn't exist."""

        def body(d: Any) -> str:
            try:
                lay = d.Layers.Item(name)
            except Exception:  # noqa: BLE001
                lay = d.Layers.Add(name)
            if lay.Freeze:
                lay.Freeze = False
            d.ActiveLayer = lay
            return f"Current layer: {name}"

        return await com.run(acad.write("set_current_layer", {"name": name}, body))

    @mcp.tool(annotations=WRITE)
    async def rename_layer(old_name: str, new_name: str) -> str:
        """Rename a layer. If a layer with the new name already exists, use merge_layers."""

        def body(d: Any) -> str:
            lay = _layer(d, old_name)
            if old_name.upper() in layer_standard.PROTECTED:
                raise AcadError(f"Layer {old_name} is a system layer and cannot be renamed.")
            try:
                d.Layers.Item(new_name)
                if new_name.upper() != old_name.upper():
                    raise AcadError(f"Layer {new_name} already exists. Use merge_layers to combine them.")
            except AcadError:
                raise
            except Exception:  # noqa: BLE001
                pass
            lay.Name = new_name
            return f"Layer {old_name} renamed to {new_name}."

        return await com.run(acad.write("rename_layer", {"old": old_name, "new": new_name}, body))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def merge_layers(sources: list[str], target: str) -> str:
        """Merge layers: move every object (including inside blocks) from `sources` to
        `target` and delete the source layers. The target is created if it doesn't exist."""

        def body(d: Any) -> str:
            try:
                d.Layers.Item(target)
            except Exception:  # noqa: BLE001
                d.Layers.Add(target)
            report = []
            for s in sources:
                if s.upper() == target.upper() or s.upper() in layer_standard.PROTECTED:
                    continue
                lay = _layer(d, s)
                lay.Lock = False
                moved = _move_all_to_layer(d, s, target)
                if str(d.GetVariable("CLAYER")).upper() == s.upper():
                    d.ActiveLayer = d.Layers.Item(target)
                try:
                    lay.Delete()
                    report.append(f"{s}: {moved} object(s) moved, layer deleted")
                except Exception:  # noqa: BLE001
                    report.append(f"{s}: {moved} object(s) moved; layer could not be deleted (still referenced)")
            return "Merge finished:\n" + "\n".join(report)

        return await com.run(acad.write("merge_layers", {"sources": sources, "target": target}, body))

    @mcp.tool(annotations=READ)
    async def check_layer_standard(standard_file: str = "") -> str:
        """Compare the drawing's layers with the office standard (JSON file) and show the
        plan: what would be renamed, merged, what is already standard and what is not.
        Changes NOTHING. Without `standard_file` it uses the built-in architecture standard
        (or the one set in AUTOCAD_MCP_LAYER_STANDARD)."""
        path = layer_standard.standard_path(standard_file)

        def fn() -> str:
            rules = layer_standard.load_standard(path)
            d = com.doc()
            p = layer_standard.plan([l.Name for l in d.Layers], rules)
            p["standard_used"] = str(path)
            p["missing_standard_layers"] = [
                r.name for r in rules if r.name.upper() not in {l.Name.upper() for l in d.Layers}
            ]
            return acad.dumps(p)

        return await com.run(fn)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def apply_layer_standard(standard_file: str = "", create_missing: bool = False,
                                   apply_properties: bool = True) -> str:
        """Standardize the drawing's layers according to the standard (run check_layer_standard first
        and show the plan to the user). Renames, merges equivalent layers, applies the standard's
        color/linetype/lineweight/plot settings and, with create_missing=True, creates the missing
        standard layers. Everything becomes a single undo step."""
        path = layer_standard.standard_path(standard_file)

        def body(d: Any) -> str:
            rules = layer_standard.load_standard(path)
            p = layer_standard.plan([l.Name for l in d.Layers], rules)
            log = []
            for old, new in p["rename"]:
                d.Layers.Item(old).Name = new
                log.append(f"renamed {old} -> {new}")
            for old, new in p["merge"]:
                lay = d.Layers.Item(old)
                lay.Lock = False
                moved = _move_all_to_layer(d, old, new)
                if str(d.GetVariable("CLAYER")).upper() == old.upper():
                    d.ActiveLayer = d.Layers.Item(new)
                try:
                    lay.Delete()
                    log.append(f"merged {old} -> {new} ({moved} objects)")
                except Exception:  # noqa: BLE001
                    log.append(f"merged {old} -> {new} ({moved} objects; old layer kept because it is in use)")
            existing = {l.Name.upper(): l for l in d.Layers}
            for r in rules:
                lay = existing.get(r.name.upper())
                if lay is None and create_missing:
                    lay = d.Layers.Add(r.name)
                    log.append(f"created {r.name}")
                if lay is not None and apply_properties:
                    try:
                        _apply_props(d, lay, r.color, r.linetype, r.lineweight_mm, plottable=r.plot,
                                     description=r.description)
                    except AcadError as exc:
                        log.append(f"warning on {r.name}: {exc}")
            if p["non_standard"]:
                log.append("non-standard (left untouched): " + ", ".join(p["non_standard"]))
            return "Standardization finished:\n" + "\n".join(log)

        return await com.run(acad.write("apply_layer_standard", {"standard": str(path)}, body))
