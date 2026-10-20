# Privacy policy

Last updated: 2026-10-19

AutoCAD MCP is a local program. It runs on your computer, started by Claude Desktop or Claude Code, and talks
to them over standard input and output.

## What it reads

- The drawings you ask Claude to work on: the one open in AutoCAD (or BricsCAD, ZWCAD, GstarCAD), or the
  .dxf/.dwg files you name.
- The layer standard file it is configured with.

What it reads is sent to Claude as the answer to Claude's request, the same way any text you type is. How
Anthropic handles that conversation is covered by Anthropic's own privacy policy.

## What it writes

- Only what you ask for: changes to the drawing, CSV files, PDF sheets, saved copies.
- A local log of every action at `%LOCALAPPDATA%\autocad-mcp\actions.log` on Windows, or
  `~/.local/state/autocad-mcp/actions.log` elsewhere. It records the tool name, its arguments and whether it
  worked. It never leaves your computer.

## What it does not do

- It opens no network port and makes no network request. There is no telemetry, analytics or crash
  reporting.
- It collects no personal data and shares nothing with the author or any third party.
- It refuses network paths and URLs, so it cannot be used to send your Windows credentials to another host.

## Contact

Questions: open an issue at https://github.com/gabrielbsandim/autocad-mcp/issues.
