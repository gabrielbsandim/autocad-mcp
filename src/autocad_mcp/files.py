"""Local file paths the server may touch, and CSV output that is safe to open in Excel."""
from __future__ import annotations

import csv
import ntpath
import os
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from autocad_mcp.errors import AcadError
from autocad_mcp.units import csv_delimiter

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_DRIVE_REMOTE = 4


def _allowed_roots() -> list[str]:
    raw = os.environ.get("AUTOCAD_MCP_ALLOWED_DIRS", "")
    return [os.path.normcase(os.path.abspath(p)) for p in raw.split(os.pathsep) if p.strip()]


def _is_network_drive(path: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    drive = ntpath.splitdrive(path)[0]
    if not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == _DRIVE_REMOTE


def safe_path(path: str, *, extensions: Iterable[str] = (), must_exist: bool = False) -> str:
    """Absolute local path, or AcadError. Network shares are refused because opening one sends the
    user's Windows credentials to that host, which text inside a drawing could otherwise trigger."""
    raw = (path or "").strip().strip('"')
    if not raw:
        raise AcadError("A file path is required.")
    if raw.startswith(("\\\\", "//")):
        raise AcadError(f"Network paths are not allowed: {raw}. Copy the file to a local folder first.")
    if "://" in raw:
        raise AcadError(f"URLs are not allowed: {raw}. Use a file on this computer.")
    if not os.path.isabs(raw) and not ntpath.isabs(raw):
        raise AcadError(f"Use a full path (for example C:\\Projects\\house.dwg), not {raw!r}.")
    full = os.path.abspath(os.path.expanduser(raw))
    if _is_network_drive(full):
        raise AcadError(f"{full} is on a network drive. Copy the file to a local folder first.")
    roots = _allowed_roots()
    norm = os.path.normcase(full)
    if roots and not any(norm == r or norm.startswith(r.rstrip("\\/") + os.sep) for r in roots):
        raise AcadError(f"{full} is outside the folders allowed by AUTOCAD_MCP_ALLOWED_DIRS.")
    exts = {e.lower() for e in extensions}
    if exts and os.path.splitext(full)[1].lower() not in exts:
        raise AcadError(f"Use one of these extensions: {', '.join(sorted(exts))}.")
    if must_exist and not os.path.isfile(full):
        raise AcadError(f"File not found: {full}")
    return full


def output_path(path: str, *, extensions: Iterable[str], overwrite: bool) -> str:
    full = safe_path(path, extensions=extensions)
    folder = os.path.dirname(full)
    if not os.path.isdir(folder):
        raise AcadError(f"Folder does not exist: {folder}")
    if os.path.exists(full) and not overwrite:
        raise AcadError(f"{full} already exists. Confirm with the user and use overwrite=True to replace it.")
    return full


def safe_cell(value: Any) -> Any:
    """Numbers stay numbers; text that Excel would run as a formula gets a leading apostrophe."""
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _cell(value: Any, decimals: int) -> str:
    cleaned = safe_cell(value)
    if isinstance(cleaned, int):
        return str(cleaned)
    if isinstance(cleaned, float):
        return _plain_number(cleaned, decimals)
    return cleaned


def _plain_number(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if csv_delimiter() == ";" else text


def write_csv(path: str, header: Sequence[str], rows: Iterable[Sequence[Any]], decimals: int = 2) -> int:
    """Write rows in the locale's CSV dialect (pt-BR: ';' and decimal comma). Returns the row count."""
    count = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=csv_delimiter())
        w.writerow([safe_cell(h) for h in header])
        for row in rows:
            w.writerow([_cell(v, decimals) for v in row])
            count += 1
    return count
