"""Connection to AutoCAD over COM.

Every COM call runs on a single dedicated thread (STA apartment): COM objects
cannot be shared between threads, and the MCP server is asynchronous. Tools use
`await run(fn)` to execute there.

Nothing here opens a network port: Claude Desktop talks to this process over
stdin/stdout, and this process talks to AutoCAD through the local COM interface.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Callable
from typing import Any, TypeVar

import pythoncom
import pywintypes
import win32com.client
from mcp.server.mcpserver.exceptions import ToolError

T = TypeVar("T")

# From most specific to most generic, so nearby AutoCAD versions also work.
PROG_IDS = [
    "AutoCAD.Application.25.1",  # 2026
    "AutoCAD.Application.25",  # 2025
    "AutoCAD.Application.24.3",  # 2024
    "AutoCAD.Application",
]

# AutoCAD busy (active command, open dialog): worth retrying.
_BUSY_HRESULTS = {
    -2147418111,  # RPC_E_CALL_REJECTED
    -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
}


class AcadError(ToolError):
    """Error with a friendly message, ready for Claude to pass on to the user.

    Inherits from ToolError so the SDK hands the message to the model; any other
    exception would arrive only as "Error executing tool"."""


def _com_init() -> None:
    pythoncom.CoInitialize()


_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="acad-com", initializer=_com_init
)


def _friendly(exc: Exception) -> AcadError:
    if isinstance(exc, AcadError):
        return exc
    if isinstance(exc, pywintypes.com_error):
        hr = exc.args[0] if exc.args else None
        detail = ""
        if len(exc.args) > 2 and exc.args[2]:
            detail = str(exc.args[2][2] or "")
        if hr in _BUSY_HRESULTS:
            return AcadError(
                "AutoCAD is busy (probably a command is running or a dialog is open). "
                "Ask the user to press Esc in AutoCAD and try again."
            )
        if hr in (-2147221021, -2147221005):  # MK_E_UNAVAILABLE / CO_E_CLASSSTRING
            return AcadError("AutoCAD is not running. Ask the user to open AutoCAD and try again.")
        if hr in (-2147023174, -2147023170, -2147417848):  # RPC server unavailable / disconnected
            _state.reset()
            return AcadError("The connection to AutoCAD was lost (it was closed or restarted). Try again.")
        return AcadError(f"AutoCAD error: {detail or exc}")
    return AcadError(str(exc))


def _call_with_retry(fn: Callable[[], T], attempts: int = 6, delay: float = 0.5) -> T:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except pywintypes.com_error as exc:
            if exc.args and exc.args[0] in _BUSY_HRESULTS:
                last = exc
                time.sleep(delay)
                continue
            raise _friendly(exc) from exc
        except AcadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _friendly(exc) from exc
    assert last is not None
    raise _friendly(last)


async def run(fn: Callable[[], T]) -> T:
    """Run `fn` on the COM thread, retrying while AutoCAD is busy."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _call_with_retry, fn)


class _State:
    app: Any = None

    def reset(self) -> None:
        self.app = None


_state = _State()


def app() -> Any:
    """The already running AutoCAD instance. Never starts AutoCAD by itself."""
    if _state.app is not None:
        try:
            _ = _state.app.Name
            return _state.app
        except Exception:  # noqa: BLE001
            _state.reset()
    for pid in PROG_IDS:
        try:
            _state.app = win32com.client.GetActiveObject(pid)
            return _state.app
        except pywintypes.com_error:
            continue
    raise AcadError("AutoCAD is not running. Ask the user to open AutoCAD and try again.")


def doc() -> Any:
    """Active document (the drawing in front in AutoCAD)."""
    a = app()
    if a.Documents.Count == 0:
        raise AcadError("No drawing is open in AutoCAD.")
    return a.ActiveDocument


def is_quiescent() -> bool:
    try:
        return bool(app().GetAcadState().IsQuiescent)
    except Exception:  # noqa: BLE001
        return True
