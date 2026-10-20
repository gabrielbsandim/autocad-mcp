"""Status, open drawings, open/new/save, current selection and undo."""
from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, READ, WRITE
from autocad_mcp.units import meters_per_unit

_SAVE_EXT = {".dwg", ".dxf"}
AC2013_DXF = 61

# Variables that may be changed. Deliberately a closed list: nothing that
# touches security (SECURELOAD, TRUSTEDPATHS etc.).
SAFE_SETVARS = {
    "INSUNITS", "LTSCALE", "PSLTSCALE", "MSLTSCALE", "DIMSCALE", "TEXTSIZE", "OSMODE",
    "ORTHOMODE", "GRIDMODE", "SNAPMODE", "LUNITS", "LUPREC", "AUNITS", "AUPREC",
    "CECOLOR", "CELTYPE", "CELWEIGHT", "FILLMODE", "LWDISPLAY", "PDMODE", "PDSIZE",
}


def _status() -> dict[str, Any]:
    b = cad.backend()
    a = cad.app()
    info: dict[str, Any] = {
        "connected": True,
        "mode": "live CAD (COM)" if b.name == "com" else "file (DXF/DWG edited without a CAD running)",
        "cad": f"{a.Name} {a.Version}",
        "read_only_mode": acad.READ_ONLY,
        "open_drawings": a.Documents.Count,
        "action_log": str(acad.LOG_FILE),
    }
    if not a.Documents.Count:
        if b.name == "file":
            info["next_step"] = "Ask the user for the full path of the .dxf or .dwg file and call open_drawing."
        return info
    d = a.ActiveDocument
    ins = int(d.GetVariable("INSUNITS"))
    factor, unit_name = meters_per_unit(ins)
    info["active_drawing"] = {
        "name": d.Name,
        "path": d.FullName or "(not saved yet)",
        "read_only_in_cad": bool(d.ReadOnly),
        "unsaved_changes": bool(d.GetVariable("DBMOD")),
        "unit_INSUNITS": f"{ins} = {unit_name}",
        "model_space_objects": d.ModelSpace.Count,
        "layers": d.Layers.Count,
        "layouts": [l.Name for l in d.Layouts if l.Name != "Model"],
        "objects_selected_now": len(acad.pickfirst(d)),
    }
    if factor is None:
        info["unit_warning"] = (
            "The drawing declares no unit (INSUNITS=0). For areas in m², ask the user whether "
            "the drawing is in mm, cm or m and pass that in the unit parameter."
        )
    return info


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ)
    async def autocad_status() -> str:
        """Check the connection and summarize the active drawing: name, folder, units, number of
        objects, layouts, the mode (live CAD or file) and whether the server is read-only.
        Use it first, to get your bearings."""
        return await cad.run(lambda: acad.dumps(_status()))

    @mcp.tool(annotations=READ)
    async def list_documents() -> str:
        """List the open drawings and show which one is active."""

        def fn() -> str:
            a = cad.app()
            active = a.ActiveDocument.Name if a.Documents.Count else None
            docs = [
                {"name": d.Name, "path": d.FullName or "(not saved)", "active": d.Name == active}
                for d in a.Documents
            ]
            return acad.dumps(docs)

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def switch_document(name: str) -> str:
        """Make one of the already open drawings active (by file name, e.g. 'Smith House.dwg')."""

        def fn() -> str:
            a = cad.app()
            for d in a.Documents:
                if d.Name.lower() == name.lower() or os.path.splitext(d.Name)[0].lower() == name.lower():
                    d.Activate()
                    return f"Active drawing is now: {d.Name}"
            raise AcadError(f"No open drawing named {name!r}. Use list_documents.")

        return await cad.run(fn)

    @mcp.tool(annotations=READ)
    async def open_drawing(file_path: str, read_only: bool = True) -> str:
        """Open a .dwg/.dxf file. By default it opens READ-ONLY (read_only=True), for safe
        queries; pass False only if the user wants to edit and save it."""

        def fn() -> str:
            if not read_only:
                acad.ensure_writable()
            path = files.safe_path(file_path, extensions=_SAVE_EXT, must_exist=True)
            a = cad.app()
            a.Documents.Open(path, bool(read_only))
            return f"Opened: {a.ActiveDocument.Name} ({'read-only' if read_only else 'editable'})"

        return await acad.act("open_drawing", {"file_path": file_path, "read_only": read_only}, fn, writes=False)

    @mcp.tool(annotations=WRITE)
    async def new_drawing(template: str = "") -> str:
        """Create a new blank drawing and make it active. Useful to try something without
        touching the user's project. Optional `template`: full path to a .dwt (or .dxf in file mode)."""

        def fn() -> str:
            a = cad.app()
            if template:
                a.Documents.Add(files.safe_path(template, extensions={".dwt", ".dxf", ".dwg"}, must_exist=True))
            else:
                a.Documents.Add()
            return f"New drawing created and active: {a.ActiveDocument.Name}"

        return await acad.act("new_drawing", {"template": template}, fn)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def save_drawing(file_path: str = "", overwrite: bool = False) -> str:
        """Save the active drawing.
        - Without `file_path`: saves to its own file (Ctrl+S). Only do this if the user asked to save.
        - With `file_path`: 'save as' (.dwg or .dxf). Never overwrites an existing file without overwrite=True."""

        def fn() -> str:
            d = cad.doc()
            if not file_path:
                if not d.FullName:
                    raise AcadError("The drawing was never saved; pass file_path (e.g. C:\\Projects\\house.dwg).")
                if d.ReadOnly:
                    raise AcadError("This drawing was opened read-only; use file_path to save a copy.")
                d.Save()
                return f"Saved: {d.FullName}"
            path = files.output_path(file_path, extensions=_SAVE_EXT, overwrite=overwrite)
            if path.lower().endswith(".dxf"):
                d.SaveAs(path, AC2013_DXF)
            else:
                d.SaveAs(path)
            return f"Saved as: {path}"

        return await acad.act("save_drawing", {"file_path": file_path, "overwrite": overwrite}, fn)

    @mcp.tool(annotations=READ)
    async def get_selection(detail: bool = False, limit: int = 200) -> str:
        """Return the objects the user has selected in AutoCAD right now (before talking to you).
        Great for requests like 'what is this I selected?' or 'compute the area of these'.
        If it comes back empty, ask the user to select the objects in AutoCAD (with no command running).
        In file mode there is no selection: use query_entities instead."""

        def fn() -> str:
            items = acad.pickfirst(cad.doc())
            return acad.dumps({
                "selected": len(items),
                "objects": [acad.summary(e, detail) for e in items[:limit]],
            })

        return await cad.run(fn)

    @mcp.tool(annotations=WRITE)
    async def undo_last(steps: int = 1) -> str:
        """Undo the last action(s) in the active drawing (like AutoCAD's U command).
        Each action made by this server is a single undo step."""
        n = max(1, min(int(steps), 20))

        def fn() -> str:
            d = cad.doc()
            cad.backend().undo(d, n)
            return f"Undid {n} step(s) in {d.Name}."

        return await acad.act("undo_last", {"steps": n}, fn)

    @mcp.tool(annotations=READ)
    async def get_variables(names: list[str]) -> str:
        """Read drawing system variables (e.g. INSUNITS, LTSCALE, DIMSCALE, CLAYER)."""

        def fn() -> str:
            d = cad.doc()
            out: dict[str, Any] = {}
            for n in names:
                try:
                    out[n.upper()] = d.GetVariable(n.upper())
                except Exception:  # noqa: BLE001
                    out[n.upper()] = "(no such variable)"
            return acad.dumps(out)

        return await cad.run(fn)

    @mcp.tool(
        annotations=WRITE,
        description="Change a drawing system variable, only from this safe list: "
        + ", ".join(sorted(SAFE_SETVARS))
        + ". Numbers are converted automatically.",
    )
    async def set_variable(name: str, value: str) -> str:
        key = name.strip().upper()
        if key not in SAFE_SETVARS:
            raise AcadError(f"Variable {key} is not in the allowed list: {', '.join(sorted(SAFE_SETVARS))}.")

        def body(d: Any) -> str:
            current = d.GetVariable(key)
            new: Any = value
            try:
                if isinstance(current, int):
                    new = int(float(str(value).replace(",", ".")))
                elif isinstance(current, float):
                    new = float(str(value).replace(",", "."))
            except ValueError as exc:
                raise AcadError(f"{key} needs a number, not {value!r}.") from exc
            d.SetVariable(key, new)
            return f"{key}: {current} -> {new}"

        return await acad.change("set_variable", {"name": key, "value": value}, body)
