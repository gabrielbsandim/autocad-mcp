"""Read-only queries: drawing summary, entity search, text and blocks."""
from __future__ import annotations

from collections import Counter
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.tools import READ


def _in_region(ent: Any, region: list[list[float]] | None) -> bool:
    if not region:
        return True
    b = acad.bbox(ent)
    if not b:
        return False
    (x1, y1), (x2, y2) = region[0][:2], region[1][:2]
    lo_x, hi_x, lo_y, hi_y = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
    return b["min"][0] >= lo_x and b["max"][0] <= hi_x and b["min"][1] >= lo_y and b["max"][1] <= hi_y


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def drawing_summary(where: str = "model") -> str:
        """Overview of the drawing: how many objects of each type, per layer, most used blocks
        and the drawing extents. where='model' (default) or 'paper' (active layout)."""

        def fn() -> str:
            d = com.doc()
            by_type: Counter[str] = Counter()
            by_layer: Counter[str] = Counter()
            blocks: Counter[str] = Counter()
            for e in acad.iter_entities(d, where):
                try:
                    t = e.ObjectName
                    by_type[t] += 1
                    by_layer[e.Layer] += 1
                    if t == "AcDbBlockReference":
                        blocks[acad.effective_block_name(e)] += 1
                except Exception:  # noqa: BLE001
                    continue
            ext = None
            try:
                ext = {"min": list(d.GetVariable("EXTMIN"))[:2], "max": list(d.GetVariable("EXTMAX"))[:2]}
            except Exception:  # noqa: BLE001
                pass
            return acad.dumps({
                "drawing": d.Name,
                "total": sum(by_type.values()),
                "by_type": dict(by_type.most_common()),
                "by_layer": dict(by_layer.most_common(40)),
                "most_used_blocks": dict(blocks.most_common(30)),
                "extents": ext,
            })

        return await com.run(fn)

    @mcp.tool(annotations=READ)
    async def query_entities(types: list[str] | None = None, layers: list[str] | None = None,
                             region: list[list[float]] | None = None, text_contains: str = "",
                             block_name: str = "", detail: bool = False, limit: int = 200,
                             where: str = "model") -> str:
        """Search drawing objects with combinable filters:
        - types: e.g. ["polyline", "text", "block", "line", "hatch", "dimension"] or AcDb* names.
        - layers: layer names.
        - region: [[x1,y1],[x2,y2]] — only objects fully inside the rectangle.
        - text_contains: text containing this substring (case-insensitive).
        - block_name: only blocks with this name.
        Returns handles, which can be used to edit the objects later."""
        tset = acad.normalize_types(types)
        lset = acad.normalize_layers(layers)
        needle = text_contains.lower().strip()
        if needle and not tset:
            tset = {"AcDbText", "AcDbMText"}
        if block_name and not tset:
            tset = {"AcDbBlockReference"}

        def fn() -> str:
            d = com.doc()
            found: list[dict[str, Any]] = []
            total = 0
            for e in acad.iter_entities(d, where):
                if not acad.matches(e, tset, lset):
                    continue
                try:
                    if needle and needle not in acad.text_value(e).lower():
                        continue
                    if block_name and acad.effective_block_name(e).lower() != block_name.lower():
                        continue
                    if not _in_region(e, region):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                total += 1
                if len(found) < limit:
                    found.append(acad.summary(e, detail))
            return acad.dumps({"found": total, "showing": len(found), "objects": found})

        return await com.run(fn)

    @mcp.tool(annotations=READ)
    async def get_entities(handles: list[str]) -> str:
        """Full details (points, bounds, color, linetype, attributes) of objects by handle."""

        def fn() -> str:
            d = com.doc()
            return acad.dumps([acad.summary(acad.by_handle(d, h), detail=True) for h in handles])

        return await com.run(fn)

    @mcp.tool(annotations=READ)
    async def list_blocks(include_unused: bool = False) -> str:
        """List the drawing's block definitions (doors, windows, furniture...) with how many
        times each one is inserted in model space and its attributes (TAGs)."""

        def fn() -> str:
            d = com.doc()
            used: Counter[str] = Counter()
            for e in acad.iter_entities(d):
                try:
                    if e.ObjectName == "AcDbBlockReference":
                        used[acad.effective_block_name(e)] += 1
                except Exception:  # noqa: BLE001
                    continue
            out = []
            for b in d.Blocks:
                try:
                    if b.IsLayout or b.IsXRef or b.Name.startswith("*"):
                        continue
                    if not include_unused and used[b.Name] == 0:
                        continue
                    tags = []
                    for i in range(b.Count):
                        it = b.Item(i)
                        if it.ObjectName == "AcDbAttributeDefinition":
                            tags.append(it.TagString)
                    out.append({"block": b.Name, "model_space_inserts": used[b.Name], "attributes": tags})
                except Exception:  # noqa: BLE001
                    continue
            out.sort(key=lambda r: -r["model_space_inserts"])
            return acad.dumps(out)

        return await com.run(fn)

    @mcp.tool(annotations=READ)
    async def list_layouts() -> str:
        """List the drawing's sheets (layouts), in tab order, with plot device and paper size."""

        def fn() -> str:
            d = com.doc()
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

        return await com.run(fn)
