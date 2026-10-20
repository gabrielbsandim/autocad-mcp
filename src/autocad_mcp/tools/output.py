"""Output: export sheets to PDF, purge and audit the drawing."""
from __future__ import annotations

import os
import re
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE

PDF_DEVICE = "DWG To PDF.pc3"
PLOT_TIMEOUT = 600.0
BACKGROUNDPLOT_OFF = 0
REGEN_ALL_VIEWPORTS = 1


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "sheet"


def _device(layout: Any, plotter: str) -> str | None:
    """None keeps the layout's own PDF page setup, so its paper size names still match."""
    if plotter:
        return plotter
    configured = str(layout.ConfigName or "")
    return None if "pdf" in configured.lower() else PDF_DEVICE


def _folder(d: Any, output_folder: str) -> str:
    if output_folder:
        return files.safe_path(output_folder)
    if d.FullName:
        return os.path.join(os.path.dirname(d.FullName), "PDF")
    return os.path.join(os.path.expanduser("~"), "Documents", "PDF")


def _choose(d: Any, layouts: list[str] | None) -> list[Any]:
    all_layouts = sorted((l for l in d.Layouts if l.Name != "Model"), key=lambda l: l.TabOrder)
    if not layouts:
        chosen = all_layouts
    else:
        wanted = {n.lower() for n in layouts}
        chosen = [l for l in all_layouts if l.Name.lower() in wanted]
        missing = wanted - {l.Name.lower() for l in chosen}
        if missing:
            raise AcadError(f"Sheet(s) not found: {', '.join(sorted(missing))}. Use list_layouts.")
    if not chosen:
        raise AcadError("The drawing has no sheets (layouts).")
    return chosen


def _plot(d: Any, layout: Any, path: str, plotter: str) -> None:
    d.ActiveLayout = layout
    d.Regen(REGEN_ALL_VIEWPORTS)
    device = _device(layout, plotter)
    ok = d.Plot.PlotToFile(path) if device is None else d.Plot.PlotToFile(path, device)
    if ok is False:
        raise AcadError("the CAD refused to plot")
    acad.wait_for_file(path, timeout=180)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def export_layouts_pdf(layouts: list[str] | None = None, output_folder: str = "",
                                 overwrite: bool = False, plotter: str = "") -> str:
        """Export sheets (layouts) to PDF, one file per sheet, using each layout's page setup
        (paper, scale, plot style). A layout already set to a PDF plotter keeps it; others use
        'DWG To PDF.pc3', or the `plotter` given (e.g. 'AutoCAD PDF (High Quality Print).pc3').
        - layouts: sheet names; empty = all of them, in tab order.
        - output_folder: destination folder; empty = a 'PDF' folder next to the drawing.
        Does not change the drawing."""

        def fn() -> str:
            if cad.backend().name == "file":
                raise AcadError("Exporting sheets to PDF needs AutoCAD (or BricsCAD, ZWCAD) running.")
            d = cad.doc()
            chosen = _choose(d, layouts)
            folder = _folder(d, output_folder)
            os.makedirs(folder, exist_ok=True)
            base = os.path.splitext(d.Name)[0]
            original_layout = d.ActiveLayout
            old_bg = d.GetVariable("BACKGROUNDPLOT")
            done, failed = [], []
            try:
                d.SetVariable("BACKGROUNDPLOT", BACKGROUNDPLOT_OFF)
                for lay in chosen:
                    path = os.path.join(folder, f"{_safe(base)} - {_safe(lay.Name)}.pdf")
                    if os.path.exists(path) and not overwrite:
                        failed.append(f"{lay.Name}: {path} already exists (use overwrite=True)")
                        continue
                    try:
                        _plot(d, lay, path, plotter)
                        done.append(path)
                    except Exception as exc:  # noqa: BLE001
                        failed.append(f"{lay.Name}: {cad.friendly(exc)}")
            finally:
                d.SetVariable("BACKGROUNDPLOT", old_bg)
                d.ActiveLayout = original_layout
            return acad.dumps({"folder": folder, "created": done, "failed": failed})

        return await acad.act("export_layouts_pdf", {"layouts": layouts, "folder": output_folder,
                                                     "plotter": plotter}, fn, limit=PLOT_TIMEOUT)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def purge_drawing() -> str:
        """Clean up the drawing (PURGE): remove unused layers, blocks, styles and linetypes.
        Makes the file smaller and tidier. Confirm with the user first."""

        def counts(d: Any) -> dict[str, int]:
            return {"layers": d.Layers.Count, "blocks": d.Blocks.Count, "text_styles": d.TextStyles.Count,
                    "linetypes": d.Linetypes.Count, "dim_styles": d.DimStyles.Count}

        def body(d: Any) -> str:
            before = counts(d)
            for _ in range(3):  # nested items only become free after the first pass
                d.PurgeAll()
            after = counts(d)
            removed = {k: before[k] - after[k] for k in before if before[k] != after[k]}
            return "Purge finished. Removed: " + (", ".join(f"{v} {k}" for k, v in removed.items()) or "nothing")

        return await acad.change("purge_drawing", {}, body)

    @mcp.tool(annotations=WRITE)
    async def audit_drawing(fix: bool = True) -> str:
        """Check the drawing's integrity (AUDIT) and, with fix=True, fix the errors found.
        Useful when the file behaves oddly, is slow or throws errors."""

        def body(d: Any) -> str:
            d.AuditInfo(bool(fix))
            found = getattr(d, "last_audit", None)
            if found:
                return f"Audit done: {found['errors']} error(s), {found['fixes']} fix(es) applied."
            return "Audit done" + (" with error fixing." if fix else " (without fixing).") + \
                " Details are on the CAD command line."

        return await acad.change("audit_drawing", {"fix": fix}, body)
