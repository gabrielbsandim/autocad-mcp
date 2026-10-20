"""Status, open/new/switch drawings, save, undo and system variables."""
from __future__ import annotations

import os
from typing import Any, Literal

from ezdxf.addons import odafc
from mcp.server.mcpserver import MCPServer

from autocad_mcp import acad, cad, files
from autocad_mcp.errors import AcadError
from autocad_mcp.tools import DESTRUCTIVE, READ, WRITE, spec
from autocad_mcp.units import meters_per_unit

DRAWING_EXT = {".dwg", ".dxf"}
TEMPLATE_EXT = {".dwt", ".dxf", ".dwg"}
AC2013_DXF = 61

# Deliberately a closed list: nothing that touches security (SECURELOAD, TRUSTEDPATHS etc.).
SAFE_SETVARS = {
    "INSUNITS", "LTSCALE", "PSLTSCALE", "MSLTSCALE", "DIMSCALE", "TEXTSIZE", "OSMODE",
    "ORTHOMODE", "GRIDMODE", "SNAPMODE", "LUNITS", "LUPREC", "AUNITS", "AUPREC",
    "CECOLOR", "CELTYPE", "CELWEIGHT", "FILLMODE", "LWDISPLAY", "PDMODE", "PDSIZE",
}


def status_info() -> dict[str, Any]:
    b = cad.backend()
    a = cad.app()
    info: dict[str, Any] = {
        "connected": True,
        "mode": "live CAD (COM)" if b.name == "com" else "file (DXF/DWG edited without a CAD running)",
        "cad": f"{a.Name} {a.Version}",
        "read_only_mode": acad.READ_ONLY,
        "open_drawings": [{"name": d.Name, "path": d.FullName or "(not saved)"} for d in a.Documents],
        "action_log": str(acad.LOG_FILE),
    }
    if b.name == "file":
        info["dwg_support"] = ("yes (ODA File Converter found)" if odafc.is_installed() else
                               "no: .dxf only. For .dwg, install the free ODA File Converter, or save as DXF.")
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


def _open(path: str, read_only: bool) -> str:
    if not read_only:
        acad.ensure_writable()
    full = files.safe_path(path, extensions=DRAWING_EXT, must_exist=True)
    a = cad.app()
    a.Documents.Open(full, bool(read_only))
    return f"Opened: {a.ActiveDocument.Name} ({'read-only' if read_only else 'editable'})"


def _new(template: str) -> str:
    acad.ensure_writable()
    a = cad.app()
    if template:
        a.Documents.Add(files.safe_path(template, extensions=TEMPLATE_EXT, must_exist=True))
    else:
        a.Documents.Add()
    return f"New drawing created and active: {a.ActiveDocument.Name}"


def _switch(name: str) -> str:
    wanted = name.lower()
    for d in cad.app().Documents:
        if wanted in (d.Name.lower(), os.path.splitext(d.Name)[0].lower()):
            d.Activate()
            return f"Active drawing is now: {d.Name}"
    raise AcadError(f"No open drawing named {name!r}. status lists the open drawings.")


def _save(d: Any, file_path: str, overwrite: bool) -> str:
    if not file_path:
        if not d.FullName:
            raise AcadError("The drawing was never saved; pass file_path (e.g. C:\\Projects\\house.dwg).")
        if d.ReadOnly:
            raise AcadError("This drawing was opened read-only; use file_path to save a copy.")
        d.Save()
        return f"Saved: {d.FullName}"
    path = files.output_path(file_path, extensions=DRAWING_EXT, overwrite=overwrite)
    if path.lower().endswith(".dxf"):
        d.SaveAs(path, AC2013_DXF)
    else:
        d.SaveAs(path)
    return f"Saved as: {path}"


def register(mcp: MCPServer) -> None:
    @mcp.tool(**spec("Status", READ))
    async def status() -> str:
        """Check the connection and summarize the situation: the mode (live CAD or file), open drawings,
        and for the active one its folder, unit, object count, layouts and current selection. Use it first."""
        return await cad.run(lambda: acad.dumps(status_info()))

    @mcp.tool(**spec("Open, create or switch drawing", WRITE))
    async def open_drawing(action: Literal["open", "new", "switch"] = "open", file_path: str = "",
                           read_only: bool = True, template: str = "", name: str = "") -> str:
        """- action='open': open the .dwg/.dxf at `file_path` (full path). READ-ONLY by default; pass
          read_only=False only when the user wants to edit and save it.
        - action='new': new blank drawing (optional `template`, full path to a .dwt), to try something
          without touching the user's project.
        - action='switch': make an already open drawing active, by `name` (e.g. 'Smith House.dwg')."""
        args = {"action": action, "file_path": file_path, "read_only": read_only, "template": template, "name": name}
        if action == "open":
            return await acad.act("open_drawing", args, lambda: _open(file_path, read_only), writes=False)
        if action == "new":
            return await acad.act("open_drawing", args, lambda: _new(template), writes=False)
        return await acad.act("open_drawing", args, lambda: _switch(name), writes=False)

    @mcp.tool(**spec("Save drawing", DESTRUCTIVE))
    async def save_drawing(file_path: str = "", overwrite: bool = False) -> str:
        """Save the active drawing. Only when the user asks.
        - Without `file_path`: saves to its own file (Ctrl+S).
        - With `file_path`: 'save as' (.dwg or .dxf). Never overwrites an existing file without overwrite=True."""
        return await acad.act("save_drawing", {"file_path": file_path, "overwrite": overwrite},
                              lambda: _save(cad.doc(), file_path, overwrite))

    @mcp.tool(**spec("Undo", WRITE))
    async def undo(steps: int = 1) -> str:
        """Undo the last action(s) in the active drawing (like the U command). Each action made by this
        server is a single step."""
        n = max(1, min(int(steps), 20))

        def fn() -> str:
            d = cad.doc()
            cad.backend().undo(d, n)
            return f"Undid {n} step(s) in {d.Name}."

        return await acad.act("undo", {"steps": n}, fn)

    @mcp.tool(**spec("Set system variable", WRITE, "Change a drawing system variable, only from this safe "
                     "list: " + ", ".join(sorted(SAFE_SETVARS)) + ". Numbers are converted automatically. "
                     "To read variables use describe_drawing(sections=['variables'])."))
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
