"""Live connection to a running CAD over Windows COM (AutoCAD, BricsCAD, ZWCAD, GstarCAD).

Nothing here opens a network port: Claude talks to this process over stdin/stdout, and this
process talks to the CAD through the local COM interface.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterable, Sequence
from typing import Any

from autocad_mcp.errors import AcadError

# From most specific to most generic, so nearby versions also work. The non-Autodesk CADs
# expose the same AutoCAD object model under their own ProgID.
PROG_IDS = [
    "AutoCAD.Application.25.1",
    "AutoCAD.Application.25",
    "AutoCAD.Application.24.3",
    "AutoCAD.Application",
    "BricscadApp.AcadApplication",
    "ZWCAD.Application",
    "GCAD.Application",
]

RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
BUSY_HRESULTS = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}
NOT_RUNNING_HRESULTS = {-2147221021, -2147221005}
DISCONNECTED_HRESULTS = {-2147023174, -2147023170, -2147417848}

UNDO_WAIT = 15.0


def prog_ids() -> list[str]:
    forced = os.environ.get("AUTOCAD_MCP_PROGID", "").strip()
    return [forced] if forced else PROG_IDS


class ComBackend:
    name = "com"

    def __init__(self) -> None:
        import pythoncom
        import pywintypes
        import win32com.client

        self._pythoncom = pythoncom
        self._com_error = pywintypes.com_error
        self._client = win32com.client
        self._app: Any = None
        self.label = "AutoCAD"

    def init_thread(self) -> None:
        self._pythoncom.CoInitialize()

    def app(self) -> Any:
        """The already running CAD. Never starts one by itself."""
        if self._app is not None:
            try:
                _ = self._app.Name
                return self._app
            except Exception:  # noqa: BLE001
                self._app = None
        for pid in prog_ids():
            try:
                self._app = self._client.GetActiveObject(pid)
            except self._com_error:
                continue
            self.label = _product(self._app)
            return self._app
        raise AcadError("AutoCAD (or BricsCAD, ZWCAD, GstarCAD) is not running. Ask the user to open it and try again.")

    def point(self, x: float, y: float, z: float = 0.0) -> Any:
        vt = self._pythoncom.VT_ARRAY | self._pythoncom.VT_R8
        return self._client.VARIANT(vt, (float(x), float(y), float(z)))

    def flat(self, values: Iterable[float]) -> Any:
        vt = self._pythoncom.VT_ARRAY | self._pythoncom.VT_R8
        return self._client.VARIANT(vt, tuple(float(v) for v in values))

    def objects(self, objs: Sequence[Any]) -> Any:
        vt = self._pythoncom.VT_ARRAY | self._pythoncom.VT_DISPATCH
        return self._client.VARIANT(vt, tuple(objs))

    def _hresult(self, exc: BaseException) -> int | None:
        if isinstance(exc, self._com_error) and exc.args:
            return exc.args[0]
        return None

    def is_busy(self, exc: BaseException) -> bool:
        return self._hresult(exc) in BUSY_HRESULTS

    def is_fatal(self, exc: BaseException) -> bool:
        return self._hresult(exc) in BUSY_HRESULTS | DISCONNECTED_HRESULTS

    def translate(self, exc: BaseException) -> AcadError | None:
        hr = self._hresult(exc)
        if hr is None:
            return None
        if hr in BUSY_HRESULTS:
            return AcadError(
                f"{self.label} is busy (probably a command is running or a dialog is open). "
                f"Ask the user to press Esc in {self.label} and try again."
            )
        if hr in NOT_RUNNING_HRESULTS:
            return AcadError(f"{self.label} is not running. Ask the user to open it and try again.")
        if hr in DISCONNECTED_HRESULTS:
            self._app = None
            return AcadError(f"The connection to {self.label} was lost (it was closed or restarted). Try again.")
        detail = ""
        if len(exc.args) > 2 and exc.args[2]:
            detail = str(exc.args[2][2] or "")
        return AcadError(f"{self.label} error: {detail or exc}")

    def quiescent(self) -> bool:
        try:
            return bool(self.app().GetAcadState().IsQuiescent)
        except AcadError:
            raise
        except Exception:  # noqa: BLE001
            return True

    def undo(self, document: Any, steps: int) -> None:
        """SendCommand only queues the command, so wait until the CAD is idle again."""
        if not self.quiescent():
            raise AcadError(f"A command is running in {self.label}. Ask the user to finish it or press Esc first.")
        document.SendCommand(f"_.UNDO {int(steps)} ")
        deadline = time.monotonic() + UNDO_WAIT
        while not self.quiescent():
            if time.monotonic() > deadline:
                raise AcadError(f"{self.label} did not finish the undo within {UNDO_WAIT:.0f} s.")
            time.sleep(0.2)


def _product(app: Any) -> str:
    try:
        name = str(app.Name)
    except Exception:  # noqa: BLE001
        return "AutoCAD"
    for known in ("BricsCAD", "ZWCAD", "GstarCAD", "AutoCAD"):
        if known.lower() in name.lower():
            return known
    return name or "AutoCAD"
