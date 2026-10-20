"""Read-only queries: drawing summary, entity search, text and blocks."""
from __future__ import annotations

from collections import Counter
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad
from autocad_mcp.tools import READ


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def drawing_summary(where: str = "model") -> str:
        """Overview of the drawing: how many objects of each type, per layer, most used blocks
        and the drawing extents. where='model' (default) or 'paper' (active layout)."""

        def fn() -> str:
            d = cad.doc()
            by_type: Counter[str] = Counter()
            by_layer: Counter[str] = Counter()
            blocks: Counter[str] = Counter()
            scan = acad.Scan()
            for e in acad.iter_entities(d, where):
                with scan.item():
                    t = acad.kind(e)
                    by_type[t] += 1
                    by_layer[str(e.Layer)] += 1
                    if t == "AcDbBlockReference":
                        blocks[acad.effective_block_name(e)] += 1
            ext = None
            try:
                ext = {"min": list(d.GetVariable("EXTMIN"))[:2], "max": list(d.GetVariable("EXTMAX"))[:2]}
            except Exception:  # noqa: BLE001
                ext = None
            return acad.dumps(scan.report({
                "drawing": d.Name,
                "total": sum(by_type.values()),
                "by_type": dict(by_type.most_common()),
                "by_layer": dict(by_layer.most_common(40)),
                "most_used_blocks": dict(blocks.most_common(30)),
                "extents": ext,
            }))

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def query_entities(types: list[str] | None = None, layers: list[str] | None = None,
                             region: list[list[float]] | None = None, text_contains: str = "",
                             block_name: str = "", detail: bool = False, limit: int = 200,
                             where: str = "model") -> str:
        """Search drawing objects with combinable filters:
        - types: e.g. ["polyline", "text", "block", "line", "hatch", "dimension", "spline"] or AcDb* names.
        - layers: layer names.
        - region: [[x1,y1],[x2,y2]], only objects fully inside the rectangle.
        - text_contains: text containing this substring (case-insensitive).
        - block_name: only blocks with this name.
        Returns handles, which can be used to edit the objects later."""
        tset = acad.normalize_types(types)
        lset = acad.normalize_layers(layers)
        needle = text_contains.lower().strip()
        if needle and not tset:
            tset = set(acad.TEXT_TYPES)
        if block_name and not tset:
            tset = {"AcDbBlockReference"}

        def fn() -> str:
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

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def get_entities(handles: list[str]) -> str:
        """Full details (points, bounds, color, linetype, attributes) of objects by handle."""

        def fn() -> str:
            d = cad.doc()
            return acad.dumps([acad.summary(acad.by_handle(d, h), detail=True) for h in handles])

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def list_blocks(include_unused: bool = False) -> str:
        """List the drawing's block definitions (doors, windows, furniture...) with how many
        times each one is inserted in model space and its attributes (TAGs)."""

        def fn() -> str:
            d = cad.doc()
            used: Counter[str] = Counter()
            scan = acad.Scan()
            for e in acad.iter_entities(d):
                with scan.item():
                    if acad.kind(e) == "AcDbBlockReference":
                        used[acad.effective_block_name(e)] += 1
            out = []
            for b in d.Blocks:
                with scan.item():
                    if b.IsLayout or b.IsXRef or b.Name.startswith("*"):
                        continue
                    if not include_unused and used[b.Name] == 0:
                        continue
                    tags = [it.TagString for it in b if acad.kind(it) == "AcDbAttributeDefinition"]
                    out.append({"block": b.Name, "model_space_inserts": used[b.Name], "attributes": tags})
            out.sort(key=lambda r: -r["model_space_inserts"])
            return acad.dumps(scan.report({"blocks": out}))

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def list_layouts() -> str:
        """List the drawing's sheets (layouts), in tab order, with plot device and paper size."""

        def fn() -> str:
            d = cad.doc()
            rows = []
            for lay in d.Layouts:
                if lay.Name == "Model":
                    continue
                rows.append({
                    "order": lay.TabOrder,
                    "name": lay.Name,
                    "plotter": lay.ConfigName or "(none)",
                    "paper": lay.CanonicalMediaName or "",
                    "plot_style": lay.StyleSheet or "",
                })
            rows.sort(key=lambda r: r["order"])
            return acad.dumps(rows)

        return await cad.run(fn)
