"""Output: export sheets to PDF, purge and audit the drawing."""
from __future__ import annotations

import os
import re
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, com
from autocad_mcp.com import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE

PDF_DEVICE = "DWG To PDF.pc3"


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "sheet"


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def export_layouts_pdf(layouts: list[str] | None = None, output_folder: str = "",
                                 overwrite: bool = False) -> str:
        """Export sheets (layouts) to PDF, one file per sheet, using each layout's page setup
        (paper, scale, plot style) with the 'DWG To PDF' plotter.
        - layouts: sheet names; empty = all of them, in tab order.
        - output_folder: destination folder; empty = a 'PDF' folder next to the drawing.
        Does not change the drawing."""

        def fn() -> str:
            d = com.doc()
            all_layouts = sorted((l for l in d.Layouts if l.Name != "Model"), key=lambda l: l.TabOrder)
            if layouts:
                wanted = {n.lower() for n in layouts}
                chosen = [l for l in all_layouts if l.Name.lower() in wanted]
                missing = wanted - {l.Name.lower() for l in chosen}
                if missing:
                    raise AcadError(f"Sheet(s) not found: {', '.join(sorted(missing))}. Use list_layouts.")
            else:
                chosen = all_layouts
            if not chosen:
                raise AcadError("The drawing has no sheets (layouts).")

            if output_folder:
                folder = output_folder
            elif d.FullName:
                folder = os.path.join(os.path.dirname(d.FullName), "PDF")
            else:
                folder = os.path.join(os.path.expanduser("~"), "Documents", "PDF")
            os.makedirs(folder, exist_ok=True)
            base = os.path.splitext(d.Name)[0]

            original_layout = d.ActiveLayout
            old_bg = d.GetVariable("BACKGROUNDPLOT")
            done, failed = [], []
            try:
                d.SetVariable("BACKGROUNDPLOT", 0)  # synchronous plot: the file exists when the call returns
                for lay in chosen:
                    path = os.path.join(folder, f"{_safe(base)} - {_safe(lay.Name)}.pdf")
                    if os.path.exists(path) and not overwrite:
                        failed.append(f"{lay.Name}: {path} already exists (use overwrite=True)")
                        continue
                    try:
                        d.ActiveLayout = lay
                        d.Regen(1)  # acAllViewports
                        ok = d.Plot.PlotToFile(path, PDF_DEVICE)
                        if ok is False:
                            raise AcadError("AutoCAD refused to plot")
                        acad.wait_for_file(path, timeout=180)
                        done.append(path)
                    except Exception as exc:  # noqa: BLE001
                        failed.append(f"{lay.Name}: {exc}")
            finally:
                try:
                    d.SetVariable("BACKGROUNDPLOT", old_bg)
                    d.ActiveLayout = original_layout
                except Exception:  # noqa: BLE001
                    pass
            acad.log_action("export_layouts_pdf", {"folder": folder}, f"{len(done)} ok, {len(failed)} failed")
            return acad.dumps({"folder": folder, "created": done, "failed": failed})

        return await com.run(fn)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def purge_drawing() -> str:
        """Clean up the drawing (PURGE): remove unused layers, blocks, styles and linetypes.
        Makes the file smaller and tidier. Confirm with the user first."""

        def body(d: Any) -> str:
            before = {"layers": d.Layers.Count, "blocks": d.Blocks.Count, "text_styles": d.TextStyles.Count,
                      "linetypes": d.Linetypes.Count, "dim_styles": d.DimStyles.Count}
            for _ in range(3):  # nested items only become free after the first pass
                d.PurgeAll()
            after = {"layers": d.Layers.Count, "blocks": d.Blocks.Count, "text_styles": d.TextStyles.Count,
                     "linetypes": d.Linetypes.Count, "dim_styles": d.DimStyles.Count}
            removed = {k: before[k] - after[k] for k in before if before[k] != after[k]}
            return "Purge finished. Removed: " + (", ".join(f"{v} {k}" for k, v in removed.items()) or "nothing")

        return await com.run(acad.write("purge_drawing", {}, body))

    @mcp.tool(annotations=WRITE)
    async def audit_drawing(fix: bool = True) -> str:
        """Check the drawing's integrity (AUDIT) and, with fix=True, fix the errors found.
        Useful when the file behaves oddly, is slow or throws errors."""

        def body(d: Any) -> str:
            d.AuditInfo(bool(fix))
            return "Audit done" + (" with error fixing." if fix else " (without fixing).") + \
                " Details are on the AutoCAD command line."

        return await com.run(acad.write("audit_drawing", {"fix": fix}, body))
