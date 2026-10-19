# AutoCAD MCP

Connects **Claude Desktop** to the **AutoCAD** instance running on your computer. You talk to Claude in plain
language and it queries, draws, organizes and exports your drawing.

## How to use (for people who draw)

1. Open **AutoCAD** and the drawing you want to work on.
2. Open **Claude Desktop**, in the **Chat** tab.
3. Ask for what you need, for example:
   - "Which drawing is open and what unit is it in?"
   - "Build the area schedule for the rooms on layer A-AREA and place the table next to the plan."
   - "How many doors and windows are in this project? Split them by type and save to Excel."
   - "Export all sheets to PDF."
   - "Check whether the layers follow the office standard."
   - "I selected some text. Move all of it to layer A-ANNO-TEXT with height 0.20."
   - "Draw the walls of a 4 x 3 m room, 15 cm thick, starting at point 0,0."
4. The chat's **+** button holds ready-made shortcuts: *Area schedule*, *Quantity takeoff*, *Export sheets to
   PDF*, *Review layers* and *Review drawing*.

**Tips**
- To talk about something specific, **select it in AutoCAD** and say "what I selected".
- Each request becomes **a single undo step**: if you don't like it, press **U** in AutoCAD or ask
  "undo that".
- Claude **never saves the file** unless you ask.
- If you see "AutoCAD is busy", press **Esc** in AutoCAD and ask again.
- Everything Claude does is logged to `%LOCALAPPDATA%\autocad-mcp\actions.log`.

## What it can do (51 tools)

| Area | Tools |
|---|---|
| Drawing and file | status, list/switch open drawings, open (read-only by default), new, save/save as (no overwriting), current selection, undo, read/change system variables (safe list) |
| Query | drawing summary, search by type/layer/region/text/block, object details, blocks, sheets |
| Draw | line, polyline, rectangle, circle, arc, **walls (double line from the centerline)**, text and MText, dimensions, hatches, insert blocks with attributes |
| Edit | move, copy (in series), rotate, scale, mirror, offset, delete, change layer/color/linetype/lineweight/text |
| Layers | list with counts, create, properties, current, rename, merge, **check and apply the office standard** |
| Areas | **room areas and perimeters** (name taken from the text inside the outline), area labels, **area schedule (table)**, CSV |
| Quantities | **block counts** (with wildcards and by attribute, e.g. TYPE=D1), lengths and hatch areas per layer, CSV |
| Output and maintenance | **export sheets to PDF**, purge, audit |

### Office layer standard

The standard lives in [`src/autocad_mcp/standards/architecture_layers.json`](src/autocad_mcp/standards/architecture_layers.json)
and is based on the AIA CAD Layer Guidelines: name, color, linetype, lineweight, whether it plots and *aliases*,
which are the old names that should become that layer (`*` allowed). Edit that file to match your office
standard, or point to another file with the `AUTOCAD_MCP_LAYER_STANDARD` variable.

## Security

- **No network.** Claude Desktop talks to the server over stdin/stdout, and the server talks to AutoCAD
  through the local Windows COM interface. No port is opened, so no website or other computer can reach it.
- **No arbitrary code execution.** There is no tool that runs AutoLISP, scripts or free-form commands; there
  are only tools with defined parameters. The single exception is `undo_last`, which sends exactly `_.UNDO n`.
- **System variables:** only a closed list can be changed. No `SECURELOAD`, `TRUSTEDPATHS` or similar.
- **Files:** "save as" never overwrites without `overwrite=True`, and opening a drawing is read-only by
  default.
- **Undo:** every action is grouped (`StartUndoMark`/`EndUndoMark`) and all of them are logged.
- **Read-only mode:** with `AUTOCAD_MCP_READONLY=1` in the configuration, every change is refused.
- **Never starts AutoCAD by itself:** it only connects to an AutoCAD that is already running.
- **Pinned dependencies:** `mcp==2.2.0`, `pydantic==2.13.5`, `pywin32==312`.

## Installation (for whoever sets it up)

Requirements: Windows, full AutoCAD (not LT) 2024–2026 and Python 3.11 or newer.

```powershell
cd C:\Users\<user>\autocad-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

In `%APPDATA%\Claude\claude_desktop_config.json`, add inside the top-level object:

```json
"mcpServers": {
  "autocad": {
    "command": "C:\\Users\\<user>\\autocad-mcp\\.venv\\Scripts\\python.exe",
    "args": ["-m", "autocad_mcp"],
    "env": { "AUTOCAD_MCP_READONLY": "0" }
  }
}
```

Then quit Claude Desktop from the system tray and open it again.

### Environment variables

| Variable | Purpose |
|---|---|
| `AUTOCAD_MCP_READONLY` | `1` = queries only, no changes |
| `AUTOCAD_MCP_LAYER_STANDARD` | path to your own layer standard JSON |
| `AUTOCAD_MCP_MAX_SCAN` | maximum number of objects scanned per query (default 200000) |

## Development

```
src/autocad_mcp/
  server.py          instructions for Claude, tool registration and shortcuts (prompts)
  com.py             COM connection on a dedicated thread, retries and friendly error messages
  acad.py            helpers: points, object summaries, undo, read-only mode, log
  geometry.py        pure geometry (area, centroid, point in polygon), testable without AutoCAD
  units.py           units (INSUNITS) and number formatting
  layer_standard.py  pure layer-standard logic
  tools/             one area per file (documents, query, draw, edit, layers, areas, quantities, output)
tests/               tests that don't need AutoCAD (pytest)
scripts/live_check.py   full test with AutoCAD running (uses a new drawing and closes it without saving)
scripts/stdio_check.py  test that mirrors Claude Desktop: stdio, undo and read-only mode
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\live_check.py     # needs AutoCAD running
```

To add a new tool: write the function in the right module under `tools/`. Reads use `await com.run(fn)`
and writes use `await com.run(acad.write("name", args, body))`, which already handles read-only mode, undo and
logging. Expected errors should raise `AcadError` with a clear message.

Inspired by [codesknight/AutoCAD-MCP](https://github.com/codesknight/AutoCAD-MCP) (COM + stdio architecture)
and [felixalmesberger/AUTOCAD-MCP](https://github.com/felixalmesberger/AUTOCAD-MCP) (document management).
No code was copied.
