"""Output: export sheets to PDF, purge and audit the drawing."""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Literal

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files, render
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, WRITE, spec

PDF_DEVICE = "DWG To PDF.pc3"
PLOT_TIMEOUT = 600.0
BATCH_TIMEOUT = 3 * 3600.0
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
            raise AcadError(f"Sheet(s) not found: {', '.join(sorted(missing))}. See describe_drawing(sections=['layouts']).")
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


def export_document(d: Any, layouts: list[str] | None, folder: str, overwrite: bool, plotter: str) -> dict[str, Any]:
    chosen = _choose(d, layouts)
    os.makedirs(folder, exist_ok=True)
    original_layout = d.ActiveLayout
    old_bg = d.GetVariable("BACKGROUNDPLOT")
    done, failed = [], []
    try:
        d.SetVariable("BACKGROUNDPLOT", BACKGROUNDPLOT_OFF)
        for lay in chosen:
            try:
                path = _target(folder, d.Name, lay.Name, "pdf", overwrite)
                _plot(d, lay, path, plotter)
                done.append(path)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{lay.Name}: {exc if isinstance(exc, FileExistsError) else cad.friendly(exc)}")
    finally:
        d.SetVariable("BACKGROUNDPLOT", old_bg)
        d.ActiveLayout = original_layout
    return {"drawing": d.Name, "folder": folder, "created": done, "failed": failed}


def _target(folder: str, drawing: str, sheet: str, ext: str, overwrite: bool) -> str:
    path = os.path.join(folder, f"{_safe(os.path.splitext(drawing)[0])} - {_safe(sheet)}.{ext}")
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"{path} already exists (use overwrite=True)")
    return path


def render_document(d: Any, layouts: list[str] | None, folder: str, overwrite: bool, fmt: str) -> dict[str, Any]:
    chosen = render.sheets(d.dxf, layouts)
    os.makedirs(folder, exist_ok=True)
    done, failed = [], []
    for lay in chosen:
        try:
            path = _target(folder, d.Name, lay.name, fmt, overwrite)
            render.RENDERERS[fmt](d.dxf, lay, path)
            done.append(path)
        except AcadError:
            raise
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{lay.name}: {exc}")
    return {"drawing": d.Name, "folder": folder, "created": done, "failed": failed}


Exporter = Callable[[Any, str], dict[str, Any]]


def exporter(layouts: list[str] | None, overwrite: bool, plotter: str, fmt: str) -> Exporter:
    if cad.backend().name == "file":
        return lambda d, folder: render_document(d, layouts, folder, overwrite, fmt)
    if fmt != "pdf":
        raise AcadError("With the CAD running, sheets are exported as PDF; format='svg' is for file mode.")
    return lambda d, folder: export_document(d, layouts, folder, overwrite, plotter)


def _active(app: Any) -> Any:
    try:
        return app.ActiveDocument
    except Exception:  # noqa: BLE001
        return None


def _expand(app: Any, entry: str) -> list[str]:
    """A drawing, every .dwg/.dxf in a folder (not its subfolders), or 'open' for every saved open drawing."""
    if entry.strip().lower() == "open":
        return [str(d.FullName) for d in app.Documents if d.FullName]
    path = files.safe_path(entry)
    if not os.path.isdir(path):
        return [path]
    return sorted(os.path.join(path, n) for n in os.listdir(path) if n.lower().endswith((".dwg", ".dxf")))


def _open_for_plot(app: Any, path: str) -> tuple[Any, bool]:
    want = os.path.normcase(os.path.abspath(path))
    for d in app.Documents:
        if os.path.normcase(str(d.FullName)) == want:
            return d, False
    return app.Documents.Open(path, True), True


def _export_file(app: Any, path: str, output_folder: str, export: Exporter) -> dict[str, Any]:
    try:
        d, opened = _open_for_plot(app, files.safe_path(path, extensions={".dwg", ".dxf"}, must_exist=True))
    except Exception as exc:  # noqa: BLE001
        return {"drawing": path, "created": [], "failed": [str(cad.friendly(exc))]}
    try:
        d.Activate()
        return export(d, _folder(d, output_folder))
    except Exception as exc:  # noqa: BLE001
        return {"drawing": path, "created": [], "failed": [str(cad.friendly(exc))]}
    finally:
        if opened:
            d.Close(False)


def export_many(app: Any, entries: list[str], output_folder: str, export: Exporter) -> dict[str, Any]:
    paths = list(dict.fromkeys(p for e in entries for p in _expand(app, e)))
    if not paths:
        raise AcadError("No drawing found to export.")
    original = _active(app)
    try:
        results = [_export_file(app, p, output_folder, export) for p in paths]
    finally:
        if original is not None:
            original.Activate()
    return {"drawings": results, "files_created": sum(len(r["created"]) for r in results),
            "failures": sum(len(r["failed"]) for r in results)}


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Export sheets to PDF", WRITE))
    async def export_pdf(layouts: list[str] | None = None, output_folder: str = "", overwrite: bool = False,
                         plotter: str = "", drawings: list[str] | None = None,
                         format: Literal["pdf", "svg"] = "pdf") -> str:
        """Export sheets (layouts) of the active drawing to PDF, one file per sheet, with each layout's page
        setup (paper, scale, plot style). A layout already set to a PDF plotter keeps it; others use
        'DWG To PDF.pc3' or the `plotter` given. `layouts`: sheet names, empty = all in tab order.
        `output_folder`: empty = a 'PDF' folder next to the drawing. Does not change the drawing.
        `drawings` exports many drawings in one go instead of the active one: full paths of .dwg files,
        folders (every .dwg inside) or "open" (every open drawing). Drawings that were closed are opened
        read-only and closed again without saving; one that fails does not stop the others.
        In file mode (Mac, LT, no CAD) the sheets are drawn by this server instead of plotted: every sheet
        with something on it, or model space ('Model') when none has; `format='svg'` needs nothing extra."""

        def fn() -> str:
            export = exporter(layouts, overwrite, plotter, format)
            if drawings:
                return acad.dumps(export_many(cad.app(), drawings, output_folder, export))
            d = cad.doc()
            return acad.dumps(export(d, _folder(d, output_folder)))

        args = {"layouts": layouts, "folder": output_folder, "plotter": plotter, "drawings": drawings,
                "format": format}
        return await acad.act("export_pdf", args, fn, limit=BATCH_TIMEOUT if drawings else PLOT_TIMEOUT)

    @mcp.tool(**spec("Purge or audit drawing", DESTRUCTIVE))
    async def maintain_drawing(action: Literal["purge", "audit"], fix: bool = True) -> str:
        """- purge: remove unused layers, blocks, styles and linetypes (smaller, tidier file). Confirm first.
        - audit: check the drawing's integrity and, with fix=True, fix the errors found (odd behaviour,
          slowness, errors). One undo step either way."""

        def counts(d: Any) -> dict[str, int]:
            return {"layers": d.Layers.Count, "blocks": d.Blocks.Count, "text_styles": d.TextStyles.Count,
                    "linetypes": d.Linetypes.Count, "dim_styles": d.DimStyles.Count}

        def purge(d: Any) -> str:
            before = counts(d)
            for _ in range(3):  # nested items only become free after the first pass
                d.PurgeAll()
            after = counts(d)
            removed = {k: before[k] - after[k] for k in before if before[k] != after[k]}
            return "Purge finished. Removed: " + (", ".join(f"{v} {k}" for k, v in removed.items()) or "nothing")

        def audit(d: Any) -> str:
            d.AuditInfo(bool(fix))
            found = getattr(d, "last_audit", None)
            if found:
                return f"Audit done: {found['errors']} error(s), {found['fixes']} fix(es) applied."
            return "Audit done" + (" with error fixing." if fix else " (without fixing).") + \
                " Details are on the CAD command line."

        return await acad.change("maintain_drawing", {"action": action, "fix": fix},
                                 purge if action == "purge" else audit)
