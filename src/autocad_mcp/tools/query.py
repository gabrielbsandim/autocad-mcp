"""Read-only queries: drawing overview (summary, layers, blocks, layouts, variables) and object search."""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad
from autocad_mcp.tools import READ, spec

Section = Literal["summary", "layers", "blocks", "layouts", "variables"]


def _summary(d: Any, where: str, scan: acad.Scan) -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    by_layer: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    for e in acad.iter_entities(d, where):
        with scan.item():
            t = acad.kind(e)
            by_type[t] += 1
            by_layer[str(e.Layer)] += 1
            if t == "AcDbBlockReference":
                blocks[acad.effective_block_name(e)] += 1
    try:
        ext = {"min": list(d.GetVariable("EXTMIN"))[:2], "max": list(d.GetVariable("EXTMAX"))[:2]}
    except Exception:  # noqa: BLE001
        ext = None
    return {
        "total": sum(by_type.values()),
        "by_type": dict(by_type.most_common()),
        "by_layer": dict(by_layer.most_common(40)),
        "most_used_blocks": dict(blocks.most_common(30)),
        "extents": ext,
    }


def _layers(d: Any, scan: acad.Scan) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for b in d.Blocks:
        if b.IsXRef:
            continue
        for e in b:
            with scan.item():
                counts[str(e.Layer).upper()] += 1
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
            "objects": counts.get(lay.Name.upper(), 0),
        })
    return rows


def _blocks(d: Any, scan: acad.Scan) -> list[dict[str, Any]]:
    used: Counter[str] = Counter()
    for e in acad.iter_entities(d):
        with scan.item():
            if acad.kind(e) == "AcDbBlockReference":
                used[acad.effective_block_name(e)] += 1
    out = []
    for b in d.Blocks:
        with scan.item():
            if b.IsLayout or b.IsXRef or b.Name.startswith(("*", "_")):
                continue
            tags = [it.TagString for it in b if acad.kind(it) == "AcDbAttributeDefinition"]
            out.append({"block": b.Name, "model_space_inserts": used[b.Name], "attributes": tags})
    out.sort(key=lambda r: -r["model_space_inserts"])
    return out


def _layouts(d: Any) -> list[dict[str, Any]]:
    rows = [{
        "order": lay.TabOrder,
        "name": lay.Name,
        "plotter": lay.ConfigName or "(none)",
        "paper": lay.CanonicalMediaName or "",
        "plot_style": lay.StyleSheet or "",
    } for lay in d.Layouts if lay.Name != "Model"]
    return sorted(rows, key=lambda r: r["order"])


def _variables(d: Any, names: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n in names:
        try:
            out[n.upper()] = d.GetVariable(n.upper())
        except Exception:  # noqa: BLE001
            out[n.upper()] = "(no such variable)"
    return out


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Describe drawing", READ))
    async def describe_drawing(sections: list[Section] | None = None, where: Literal["model", "paper"] = "model",
                               variables: list[str] | None = None) -> str:
        """Overview of the active drawing. `sections` (default ['summary']):
        - summary: object count per type and per layer, most used blocks, extents (`where`: model or paper).
        - layers: each layer's color, linetype, lineweight, on/frozen/locked/plot and object count.
        - blocks: block definitions (doors, windows, furniture) with insert count and attribute tags.
        - layouts: sheets in tab order with plotter and paper.
        - variables: the system variables named in `variables` (e.g. ['INSUNITS', 'LTSCALE', 'CLAYER'])."""
        wanted = sections or ["summary"]

        def fn() -> str:
            d = cad.doc()
            scan = acad.Scan()
            out: dict[str, Any] = {"drawing": d.Name}
            if "summary" in wanted:
                out["summary"] = _summary(d, where, scan)
            if "layers" in wanted:
                out["layers"] = _layers(d, scan)
            if "blocks" in wanted:
                out["blocks"] = _blocks(d, scan)
            if "layouts" in wanted:
                out["layouts"] = _layouts(d)
            if "variables" in wanted:
                out["variables"] = _variables(d, variables or ["INSUNITS", "CLAYER", "LTSCALE", "DIMSCALE"])
            return acad.dumps(scan.report(out))

        return await cad.run(fn)

    @mcp.tool(**spec("Find objects", READ))
    async def find_entities(types: list[str] | None = None, layers: list[str] | None = None,
                            region: list[list[float]] | None = None, text_contains: str = "",
                            block_name: str = "", handles: list[str] | None = None, detail: bool = False,
                            limit: int = 200, where: Literal["model", "paper"] = "model") -> str:
        """Find drawing objects and return their handles (used by the editing tools).
        - handles: details of these objects; ['selection'] = what the user selected in the CAD now
          (empty in file mode). detail=True adds points, bounds, color, linetype.
        - Otherwise combine filters: types (e.g. ["polyline", "text", "block", "line", "hatch", "dimension",
          "spline"] or AcDb* names), layers, region [[x1,y1],[x2,y2]] (fully inside), text_contains
          (case-insensitive), block_name."""
        tset = acad.normalize_types(types)
        lset = acad.normalize_layers(layers)
        needle = text_contains.lower().strip()
        if needle and not tset:
            tset = set(acad.TEXT_TYPES)
        if block_name and not tset:
            tset = {"AcDbBlockReference"}

        def by_handles() -> str:
            d = cad.doc()
            items = acad.resolve_handles(d, handles or [])
            return acad.dumps({"found": len(items), "objects": [acad.summary(e, detail or len(items) <= 20)
                                                                for e in items[:limit]]})

        def search() -> str:
            d = cad.doc()
            found: list[dict[str, Any]] = []
            total = 0
            scan = acad.Scan()
            for e in acad.iter_entities(d, where):
                with scan.item():
                    if not acad.matches(e, tset, lset):
                        continue
                    if needle and needle not in acad.text_value(e).lower():
                        continue
                    if block_name and acad.effective_block_name(e).lower() != block_name.lower():
                        continue
                    if not acad.inside_region(e, region):
                        continue
                    total += 1
                    if len(found) < limit:
                        found.append(acad.summary(e, detail))
            return acad.dumps(scan.report({"found": total, "showing": len(found), "objects": found}))

        return await cad.run(by_handles if handles else search)
