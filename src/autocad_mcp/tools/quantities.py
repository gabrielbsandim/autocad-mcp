"""Quantity takeoff: block counts (doors, windows, fixtures...) and lengths per layer."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files, takeoff
from autocad_mcp.tools import READ, WRITE
from autocad_mcp.tools.areas import UNIT_HELP, _beside_drawing
from autocad_mcp.units import fmt_num


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def count_blocks(names: list[str] | None = None, layers: list[str] | None = None,
                           region: list[list[float]] | None = None, group_by_attribute: str = "") -> str:
        """Block quantity takeoff (doors, windows, fixtures, lights, furniture...).
        - names: filter by name, wildcards allowed (e.g. ["DOOR*", "WINDOW*"]).
        - layers: only blocks on these layers. region: [[x1,y1],[x2,y2]] to count only part of the drawing (e.g. one floor).
        - group_by_attribute: split the count by the value of an attribute (e.g. "TYPE" -> D1, D2, W1...)."""

        def fn() -> str:
            return acad.dumps(takeoff.count_blocks(cad.doc(), names, layers, region, group_by_attribute))

        return await cad.run(fn)

    @mcp.tool(annotations=READ, description=(
        "Lengths per layer: sums the length (m) of lines, polylines, arcs, circles, splines and ellipses and the "
        "area (m²) of hatches. Useful for baseboards, thresholds, walls (centerline length), ceilings, hatched "
        "floors. Filter with `layers`. " + UNIT_HELP))
    async def lengths_by_layer(layers: list[str] | None = None, unit: str = "auto") -> str:
        def fn() -> str:
            return acad.dumps(takeoff.lengths_by_layer(cad.doc(), layers, unit))

        return await cad.run(fn)

    @mcp.tool(annotations=WRITE)
    async def export_quantities_csv(file_path: str = "", names: list[str] | None = None,
                                    layers: list[str] | None = None, group_by_attribute: str = "",
                                    overwrite: bool = False) -> str:
        """Export the block quantity takeoff to CSV.
        Without `file_path`, saves it next to the drawing as '<drawing> - quantities.csv'."""

        def fn() -> str:
            d = cad.doc()
            path = files.output_path(file_path or _beside_drawing(d, "quantities.csv"), extensions={".csv"},
                                     overwrite=overwrite)
            data = takeoff.count_blocks(d, names, layers, None, group_by_attribute)
            tag = group_by_attribute.upper()
            header = ["Block"] + ([tag] if tag else []) + ["Count", "Layers"]
            rows = []
            for r in data["items"]:
                layers_text = ", ".join(f"{k} ({v})" for k, v in r["layers"].items())
                rows.append([r["block"]] + ([r[tag]] if tag else []) + [r["count"], layers_text])
            rows.append(["TOTAL"] + ([""] if tag else []) + [data["total_blocks"], ""])
            files.write_csv(path, header, rows)
            return (f"Quantity takeoff saved to {path} ({len(data['items'])} type(s), "
                    f"{fmt_num(data['total_blocks'], 0)} block(s)).")

        return await acad.act("export_quantities_csv", {"file_path": file_path, "names": names}, fn)
