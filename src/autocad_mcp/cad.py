"""Which CAD the server talks to, and the single worker thread every CAD call runs on.

COM objects cannot be shared between threads and the MCP server is asynchronous, so every
call goes through `await run(fn)`. The file backend uses the same thread, which keeps one
drawing from being edited by two requests at once.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from autocad_mcp.errors import AcadError

T = TypeVar("T")

BUSY_ATTEMPTS = 6
BUSY_DELAY = 0.5
READY_WAIT = 5.0


def timeout() -> float:
    return float(os.environ.get("AUTOCAD_MCP_TIMEOUT", "60"))


class Backend(Protocol):
    name: str
    label: str

    def init_thread(self) -> None: ...
    def app(self) -> Any: ...
    def point(self, x: float, y: float, z: float = 0.0) -> Any: ...
    def flat(self, values: Any) -> Any: ...
    def objects(self, objs: Any) -> Any: ...
    def translate(self, exc: BaseException) -> AcadError | None: ...
    def is_busy(self, exc: BaseException) -> bool: ...
    def is_fatal(self, exc: BaseException) -> bool: ...
    def quiescent(self) -> bool: ...
    def undo(self, document: Any, steps: int) -> None: ...


def configured_mode() -> str:
    value = os.environ.get("AUTOCAD_MCP_BACKEND", "auto").strip().lower()
    if value in ("com", "file"):
        return value
    return "com" if sys.platform == "win32" else "file"


class _State:
    backend: Backend | None = None
    executor: concurrent.futures.ThreadPoolExecutor | None = None
    inflight: tuple[float, float] | None = None


_state = _State()


def _create(mode: str) -> Backend:
    if mode == "com":
        from autocad_mcp.com import ComBackend

        return ComBackend()
    from autocad_mcp.filecad import FileBackend

    return FileBackend()


def backend() -> Backend:
    if _state.backend is None:
        _state.backend = _create(configured_mode())
    return _state.backend


def use(new_backend: Backend | None) -> None:
    """Swap the backend (tests, or a future mode switch). Starts a fresh worker thread."""
    if _state.executor is not None:
        _state.executor.shutdown(wait=False)
    _state.backend = new_backend
    _state.executor = None
    _state.inflight = None


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    if _state.executor is None:
        _state.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cad", initializer=backend().init_thread
        )
    return _state.executor


def app() -> Any:
    return backend().app()


def doc() -> Any:
    """Active document (the drawing in front)."""
    a = app()
    if a.Documents.Count == 0:
        if backend().name == "file":
            raise AcadError("No drawing is open. Use open_drawing with the full path of a .dxf or .dwg file.")
        raise AcadError(f"No drawing is open in {backend().label}.")
    return a.ActiveDocument


def point(x: float, y: float, z: float = 0.0) -> Any:
    return backend().point(x, y, z)


def flat(values: Any) -> Any:
    return backend().flat(values)


def objects(objs: Any) -> Any:
    return backend().objects(objs)


def friendly(exc: BaseException) -> AcadError:
    if isinstance(exc, AcadError):
        return exc
    return backend().translate(exc) or AcadError(f"{backend().label} error: {exc}")


def wait_until_ready() -> None:
    """Writes never start while the user is in the middle of a command."""
    b = backend()
    deadline = time.monotonic() + READY_WAIT
    while not b.quiescent():
        if time.monotonic() > deadline:
            raise AcadError(
                f"{b.label} is busy (a command is running or a dialog is open). Nothing was changed. "
                f"Ask the user to press Esc in {b.label} and try again."
            )
        time.sleep(0.25)


def _attempts(fn: Callable[[], T], retry: bool) -> T:
    b = backend()
    tries = BUSY_ATTEMPTS if retry else 1
    for attempt in range(tries):
        try:
            return fn()
        except AcadError:
            raise
        except Exception as exc:  # noqa: BLE001
            if attempt < tries - 1 and b.is_busy(exc):
                time.sleep(BUSY_DELAY)
                continue
            raise friendly(exc) from exc
    raise AcadError(f"{b.label} stayed busy.")


def _call(fn: Callable[[], T], retry: bool, limit: float) -> T:
    _state.inflight = (time.monotonic(), limit)
    try:
        return _attempts(fn, retry)
    finally:
        _state.inflight = None


def _stuck() -> bool:
    current = _state.inflight
    return current is not None and time.monotonic() - current[0] > current[1]


async def run(fn: Callable[[], T], *, retry: bool = True, limit: float | None = None) -> T:
    """Run `fn` on the CAD thread. Reads retry while the CAD is busy; writes pass retry=False
    through acad.write, because repeating a half-done write would draw everything twice."""
    seconds = limit or timeout()
    label = backend().label
    if _stuck():
        raise AcadError(
            f"{label} has not answered an earlier request yet. It is probably showing a dialog: "
            f"ask the user to close it or press Esc in {label}, then try again."
        )
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_executor(), _call, fn, retry, seconds)
    try:
        return await asyncio.wait_for(asyncio.shield(future), seconds)
    except TimeoutError as exc:
        raise AcadError(
            f"{label} did not answer within {seconds:.0f} s. It is probably showing a dialog or running a "
            f"command: ask the user to close it or press Esc. If this request changes the drawing, check "
            f"the drawing before repeating it."
        ) from exc
