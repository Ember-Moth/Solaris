"""asyncio on a dedicated thread, results delivered on the Qt thread.

The models are asyncio code (httpx, host bridge). The app runs one asyncio loop on a
background thread; ViewModels ``submit`` coroutines and receive their result or error
through a queued signal, so every callback runs on the GUI thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

Callback = Callable[[Any], None]
ErrorCallback = Callable[[BaseException], None]


class AsyncRunner(QObject):
    _deliver = Signal(object)  # a zero-argument callable to run on the Qt thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="solaris-asyncio", daemon=True)
        self._started = False
        self._stopping = False
        self._finalize: Callable[[], Coroutine[Any, Any, None]] | None = None
        self._deliver.connect(self._on_deliver, Qt.ConnectionType.QueuedConnection)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def start(self) -> None:
        if not self._started and not self._stopping:
            self._started = True
            self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            try:
                if self._finalize is not None:
                    self._loop.run_until_complete(self._finalize())
            finally:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.close()

    def shutdown(
        self,
        timeout_s: float = 3.0,
        *,
        finalize: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """Cancel producers, then run the final local flush while the loop is still alive."""
        if self._stopping:
            return
        self._finalize = finalize
        self._stopping = True
        if not self._started or not self._thread.is_alive():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout_s)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_result: Callback | None = None,
        on_error: ErrorCallback | None = None,
        *,
        on_cancel: Callable[[], None] | None = None,
        is_current: Callable[[], bool] | None = None,
    ) -> Future[Any]:
        """Run ``coro`` on the asyncio thread; callbacks fire later on the Qt thread.

        After ``shutdown()`` the coroutine is closed and a cancelled future is returned, so a
        timer that fires during teardown cannot raise into the Qt event loop."""
        if not self._started and not self._stopping:
            self.start()
        if not self._alive():
            coro.close()
            dropped: Future[Any] = Future()
            dropped.cancel()
            return dropped
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(
            partial(self._finished, on_result, on_error, on_cancel, is_current)
        )
        return future

    def _alive(self) -> bool:
        return not self._stopping and not self._loop.is_closed() and self._thread.is_alive()

    def _finished(
        self,
        on_result: Callback | None,
        on_error: ErrorCallback | None,
        on_cancel: Callable[[], None] | None,
        is_current: Callable[[], bool] | None,
        future: Future[Any],
    ) -> None:
        callback: Callable[[], None] | None = None
        if future.cancelled():
            callback = on_cancel
        elif (error := future.exception()) is not None:
            callback = partial(on_error, error) if on_error else None
        elif on_result is not None:
            callback = partial(on_result, future.result())
        if callback is not None:

            def deliver() -> None:
                # Read ViewModel state only on the Qt thread, including callbacks
                # which were already queued when the account changed.
                if is_current is None or is_current():
                    callback()

            self._deliver.emit(deliver)

    def _on_deliver(self, callback: object) -> None:
        if not self._stopping and callable(callback):
            callback()
