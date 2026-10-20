# AutoCAD MCP

Connects **Claude** to your CAD drawings. You talk to Claude in plain language and it queries, draws, organizes
and exports the drawing.

- **Windows:** drives the CAD that is open: AutoCAD (full, not LT), BricsCAD, ZWCAD or GstarCAD.
- **Mac, AutoCAD LT, or no CAD at all:** edits `.dxf` files directly (and `.dwg` with the free
  [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) installed).

## How to use (for people who draw)

1. Open the drawing in your CAD (on a Mac, just tell Claude the file's path).
2. Ask for what you need in Claude, for example:
   - "Which drawing is open and what unit is it in?"
   - "Build the area schedule for the rooms and place the table next to the plan."
   - "How many doors and windows are there? Split them by type and save it for Excel."
   - "Export all sheets to PDF."
   - "Put the layers in the AsBEA standard."
   - "I selected some text. Move all of it to layer ARQ-___-TXT with height 0.20."
   - "Draw the walls of a 4 x 3 m room, 15 cm thick, starting at 0,0."
3. In Claude Desktop the **+** button holds ready-made shortcuts: *Area schedule*, *Quantity takeoff*,
   *Export sheets to PDF*, *Review layers* and *Review drawing*.

**Tips**
- Select objects in the CAD and say "what I selected".
- Each request is **one undo step**: press **U** in the CAD, or ask "undo that".
- Claude **never saves the file** unless you ask.
- "The CAD is busy": press **Esc** in the CAD and ask again.

## Install

### Claude Desktop (Windows and Mac)

Download `autocad-mcp-<version>.mcpb` from the [latest release](https://github.com/gabrielbsandim/autocad-mcp/releases/latest)
and double-click it. Claude Desktop shows the settings (read-only, projects folder, layer standard, number
format) and installs it. It needs [uv](https://docs.astral.sh/uv/getting-started/installation/), which fetches
Python and the dependencies the first time.

### Claude Code

```
/plugin install autocad --marketplace gabrielbsandim/autocad-mcp
```

### By hand

With [uv](https://docs.astral.sh/uv/) installed, clone the repository and add to the Claude Desktop config
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json`
on a Mac):

```json
{
  "mcpServers": {
    "autocad": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\autocad-mcp", "--no-dev", "autocad-mcp"],
      "env": { "AUTOCAD_MCP_LAYER_STANDARD": "asbea" }
    }
  }
}
```

Then quit Claude Desktop completely and open it again.

## What it can do (22 tools)

| Area | Tools |
|---|---|
| Drawing and file | `status`, `open_drawing` (open read-only by default, new, switch), `save_drawing` (never overwrites), `undo`, `set_variable` (safe list) |
| Query | `describe_drawing` (summary, layers, blocks, sheets, variables), `find_entities` (by type, layer, region, text, block, handle or selection) |
| Draw | `draw`: lines, polylines, rectangles, circles, arcs, **walls from a centerline**, text, dimensions, hatches and blocks with attributes, many at once in one undo step |
| Edit | `transform` (move, copy in series, rotate, scale, mirror, offset), `delete_entities`, `change_properties` |
| Layers | `manage_layer`, `merge_layers`, **`check_layer_standard` and `apply_layer_standard`** (AsBEA, AIA or yours) |
| Areas and quantities | **`room_areas`**, `label_rooms`, **`takeoff`** (blocks by type, lengths and hatch areas per layer), `place_table`, `export_csv` |
| Output | **`export_pdf`** (every sheet with its own page setup), `maintain_drawing` (purge, audit) |

Every tool has a title and says whether it only reads, changes the drawing, or can destroy something, so
Claude asks before the risky ones.

### Layer standards

Two come built in: **AsBEA** (`asbea`, Brazilian: `ARQ-ALV`, `ARQ-POR`, `ARQ-___-CTA`...) and **AIA**
(`aia`: `A-WALL`, `A-DOOR`...). Each layer has a color, linetype, lineweight, plot flag and *aliases*, the
old names that should become it (`PAREDE*`, `WALL*`, `A-WALL`...), so a drawing from either template, or
from no template, is converted. Copy
[`asbea_layers.json`](src/autocad_mcp/standards/asbea_layers.json), edit it to your office's names and point
`AUTOCAD_MCP_LAYER_STANDARD` at the copy.

### Live CAD or file mode

| | Live CAD (Windows) | File mode |
|---|---|---|
| Works with | AutoCAD full, BricsCAD, ZWCAD, GstarCAD, already open | `.dxf`; `.dwg` through ODA File Converter |
| Platforms | Windows | Windows, Mac, Linux |
| Selection, PDF export | yes | no |
| Saving | when you ask | changes stay in memory until `save_drawing` |

AutoCAD LT has no automation interface, so with LT use file mode: save as DXF (or install ODA File Converter
for DWG). The ODA converter is free for personal use; check its license before using it in a company.

## Security

- **No network.** Claude talks to the server over stdin/stdout; the server talks to the CAD through the local
  Windows COM interface. No port is opened.
- **No arbitrary code.** No tool runs AutoLISP, scripts or free-form commands. The one exception is `undo`,
  which sends exactly `_.UNDO n`.
- **System variables:** only a closed list can be changed. No `SECURELOAD`, `TRUSTEDPATHS` or similar.
- **Files:** saving never overwrites without `overwrite=True`, and opening is read-only by default. Only
  full local paths: network shares, mapped network drives and URLs are refused, because opening one would
  send your Windows credentials to that host. `AUTOCAD_MCP_ALLOWED_DIRS` confines every read and write.
- **CSV:** cells Excel would run as a formula get a leading apostrophe.
- **Undo:** every change is grouped. If it fails halfway, what it already did is undone, so the drawing is
  never left half-changed. Every action, including failures, is logged locally.
- **Busy or stuck CAD:** reads retry while the CAD is busy; changes never retry (a repeated write would
  draw twice) and wait for it to be idle. No answer in 60 s returns an error instead of hanging Claude.
- **Read-only mode** refuses every change, save and export.
- **Never starts the CAD by itself.**
- **Pinned dependencies,** locked with hashes in `uv.lock`.

See [PRIVACY.md](PRIVACY.md): nothing is collected or sent anywhere.

### Settings

| Variable | Purpose |
|---|---|
| `AUTOCAD_MCP_READONLY` | `1` = questions only, no changes |
| `AUTOCAD_MCP_LAYER_STANDARD` | `asbea`, `aia` (default) or the path of your own JSON |
| `AUTOCAD_MCP_LOCALE` | `pt-BR` (default: `1.234,50`, CSV with `;`) or `en-US` (`1,234.50`, CSV with `,`) |
| `AUTOCAD_MCP_ALLOWED_DIRS` | folders it may read and write, separated by `;` on Windows and `:` elsewhere |
| `AUTOCAD_MCP_BACKEND` | `auto` (default: live CAD on Windows, files elsewhere), `com` or `file` |
| `AUTOCAD_MCP_PROGID` | force one CAD, e.g. `BricscadApp.AcadApplication` |
| `AUTOCAD_MCP_TIMEOUT` | seconds to wait for the CAD (default 60) |
| `AUTOCAD_MCP_MAX_SCAN` | most objects scanned per query (default 200000) |

The action log is at `%LOCALAPPDATA%\autocad-mcp\actions.log` on Windows and
`~/.local/state/autocad-mcp/actions.log` elsewhere.

## Development

```
src/autocad_mcp/
  server.py          instructions for Claude, tool registration and shortcuts (prompts)
  cad.py             the CAD thread: backend choice, busy retries for reads, timeouts
  com.py             live backend: AutoCAD and its clones over Windows COM, friendly errors
  filecad.py         file backend: the same object model over ezdxf, for DXF/DWG without a CAD
  acad.py            object helpers, writes with undo and rollback, scans that report skips, log
  rooms.py           room outlines, names, areas
  takeoff.py         block counts, lengths and hatch areas per layer
  reports.py         schedules (rooms, blocks, lengths) shared by tables and CSV
  files.py           path checks and Excel-safe CSV
  geometry.py        pure geometry (areas with arcs, interior points, offsets)
  units.py           INSUNITS and pt-BR / en-US number formatting
  layer_standard.py  pure layer-standard logic; standards/ holds AsBEA and AIA
  tools/             one area per file
tests/               every tool end to end over MCP against a real DXF, on Windows, macOS and Linux
scripts/stdio_check.py  the server as Claude runs it: a child process over stdio (--live for the CAD)
scripts/live_check.py   every tool against the CAD that is open, on a new drawing closed without saving
```

```
uv sync --extra dev
uv run pytest
uv run python scripts/stdio_check.py
uv run python scripts/live_check.py     # Windows, with the CAD open
```

A new tool goes in the right module under `tools/`, registered with `spec(title, READ | WRITE | DESTRUCTIVE)`.
Reads use `await cad.run(fn)`; drawing changes use `await acad.change("name", args, body)`, which handles
read-only mode, waits for an idle CAD, groups the undo, rolls back a failure and logs; other writes (files)
use `await acad.act("name", args, fn)`. Loops over objects go through `acad.Scan`, so an unreadable object is
reported, never silently dropped. Expected errors raise `AcadError` with a clear message.

Releases: push a `v*` tag and the workflow tests, packs the `.mcpb` and publishes it.

Inspired by [codesknight/AutoCAD-MCP](https://github.com/codesknight/AutoCAD-MCP) (COM + stdio architecture)
and [felixalmesberger/AUTOCAD-MCP](https://github.com/felixalmesberger/AUTOCAD-MCP) (document management).
No code was copied.
