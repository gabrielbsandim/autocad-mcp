"""Sheets to SVG or PDF without a CAD (file mode), through ezdxf's drawing add-on."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration

from autocad_mcp.errors import AcadError

MODEL = "Model"
MODEL_PAGE_MM = (420.0, 297.0)
PDF_HELP = ("PDF without a CAD needs the optional 'pdf' extra (matplotlib), which the Claude Desktop bundle "
            "and the Claude Code plugin include. Use format='svg' (opens in any browser, which can print it to "
            "PDF), or install it with: uv sync --extra pdf")
CONFIG = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)


def sheets(dxf: Any, wanted: list[str] | None) -> list[Any]:
    """The layouts to render. By default every sheet with something on it, or model space when none has."""
    by_name = {n.lower(): dxf.layouts.get(n) for n in dxf.layouts.names_in_taborder()}
    if wanted:
        missing = [n for n in wanted if n.lower() not in by_name]
        if missing:
            raise AcadError(f"Sheet(s) not found: {', '.join(missing)}. Model space is called 'Model'.")
        return [by_name[n.lower()] for n in wanted]
    papers = [l for l in by_name.values() if not l.is_modelspace and len(l)]
    return papers or [dxf.modelspace()]


def _page(dxf_layout: Any) -> layout.Page:
    if not dxf_layout.is_modelspace:
        page = layout.Page.from_dxf_layout(dxf_layout)
        if page.width > 0 and page.height > 0:
            return page
    return layout.Page(*MODEL_PAGE_MM, margins=layout.Margins.all(10))


def render_svg(dxf: Any, dxf_layout: Any, path: str) -> None:
    backend = svg.SVGBackend()
    Frontend(RenderContext(dxf), backend, config=CONFIG).draw_layout(dxf_layout)
    Path(path).write_text(backend.get_string(_page(dxf_layout)), encoding="utf-8")


def render_pdf(dxf: Any, dxf_layout: Any, path: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except ImportError as exc:
        raise AcadError(PDF_HELP) from exc
    page = _page(dxf_layout)
    fig = plt.figure(figsize=(page.width_in_mm / 25.4, page.height_in_mm / 25.4))
    try:
        ax = fig.add_axes((0, 0, 1, 1))
        Frontend(RenderContext(dxf), MatplotlibBackend(ax), config=CONFIG).draw_layout(dxf_layout, finalize=True)
        fig.savefig(path, format="pdf")
    finally:
        plt.close(fig)


RENDERERS = {"pdf": render_pdf, "svg": render_svg}
