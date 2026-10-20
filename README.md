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
  default. Only full local paths are accepted: network shares (`\\server\share`), mapped network drives
  and URLs are refused, because opening one would send the user's Windows credentials to that host. Set
  `AUTOCAD_MCP_ALLOWED_DIRS` to confine every read and write to the folders you list.
- **CSV:** cells that Excel would run as a formula (starting with `=`, `+`, `-`, `@`) get a leading
  apostrophe.
- **Undo:** every action is grouped (`StartUndoMark`/`EndUndoMark`). If an action fails halfway, what it
  already did is undone, so the drawing is never left half-changed. Every action, including failures, is
  logged.
- **Busy or stuck CAD:** queries retry while AutoCAD is busy; changes never retry (a repeated write would
  draw everything twice) and wait for AutoCAD to be idle first. A request that gets no answer in 60 s
  (`AUTOCAD_MCP_TIMEOUT`) returns an error instead of hanging Claude.
- **Read-only mode:** with `AUTOCAD_MCP_READONLY=1` in the configuration, every change is refused,
  including saving, CSV and PDF exports.
- **Never starts AutoCAD by itself:** it only connects to an AutoCAD that is already running.
- **Pinned dependencies:** exact versions in `pyproject.toml`, locked with hashes in `uv.lock`.

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
| `AUTOCAD_MCP_ALLOWED_DIRS` | folders the server may read and write, separated by `;` on Windows (default: any local folder) |
| `AUTOCAD_MCP_TIMEOUT` | seconds to wait for AutoCAD before giving up (default 60) |
| `AUTOCAD_MCP_LOCALE` | `pt-BR` (default: `1.234,50`, CSV with `;`) or `en-US` (`1,234.50`, CSV with `,`) |

## Development

```
src/autocad_mcp/
  server.py          instructions for Claude, tool registration and shortcuts (prompts)
  cad.py             the CAD thread: backend choice, busy retries for reads, timeouts
  com.py             live backend: AutoCAD over Windows COM, friendly error messages
  filecad.py         file backend: the same object model over ezdxf, for DXF/DWG without a CAD
  acad.py            helpers: object summaries, writes with undo and rollback, scans that report skips, log
  rooms.py           room areas: outlines, names, totals
  takeoff.py         block counts, lengths and hatch areas per layer
  files.py           path checks and Excel-safe CSV
  geometry.py        pure geometry (areas with arcs, interior points, offsets)
  units.py           units (INSUNITS) and pt-BR / en-US number formatting
  layer_standard.py  pure layer-standard logic
  tools/             one area per file (documents, query, draw, edit, layers, areas, quantities, output)
tests/               every tool end to end against a real DXF drawing, on Windows, macOS and Linux
scripts/live_check.py   full test with AutoCAD running (uses a new drawing and closes it without saving)
scripts/stdio_check.py  test that mirrors Claude Desktop: stdio, undo and read-only mode
```

```
uv sync --extra dev
uv run pytest
uv run python scripts/live_check.py     # Windows, needs AutoCAD running
```

The tests run every tool through MCP against the file backend, which mirrors the AutoCAD object
model, so they need no AutoCAD. `live_check.py` is still the check against the real thing.

To add a new tool: write the function in the right module under `tools/`. Reads use `await cad.run(fn)`;
drawing changes use `await acad.change("name", args, body)`, which handles read-only mode, waits for an
idle CAD, groups the undo, rolls back a failure and logs; other actions that write files use
`await acad.act("name", args, fn)`. Loops over drawing objects go through `acad.Scan` so an unreadable
object is reported, never silently dropped. Expected errors raise `AcadError` with a clear message.

Inspired by [codesknight/AutoCAD-MCP](https://github.com/codesknight/AutoCAD-MCP) (COM + stdio architecture)
and [felixalmesberger/AUTOCAD-MCP](https://github.com/felixalmesberger/AUTOCAD-MCP) (document management).
No code was copied.
