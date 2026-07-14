"""Nuke's native main-thread bridge."""

from __future__ import annotations

import threading
from typing import Any, Callable

from dcc_mcp_core.host import QueueDispatcher


class NukeDispatcher:
    """Route core host-queue work through Nuke's Qt event loop."""

    def __init__(self, host_dispatcher: Any | None = None) -> None:
        self._host_dispatcher = host_dispatcher or QueueDispatcher()
        self._update_callback = self._pump_host_queue
        self._timer: Any | None = None

    @property
    def host_dispatcher(self) -> Any:
        """Expose the core queue attached to HTTP main-affinity routing."""
        return self._host_dispatcher

    def start(self) -> None:
        """Install a Qt timer that drains the queue on Nuke's UI thread."""
        if self._timer is not None:
            return
        timer = self._new_qt_timer()
        timer.setInterval(16)
        timer.timeout.connect(self._update_callback)
        timer.start()
        self._timer = timer

    def stop(self) -> None:
        """Detach the UI callback and reject any remaining queued work."""
        timer = self._timer
        if timer is not None:
            timer.stop()
            timer.timeout.disconnect(self._update_callback)
            self._timer = None
        self._host_dispatcher.shutdown()

    @staticmethod
    def _new_qt_timer() -> Any:
        """Create a timer using the Qt binding bundled with this Nuke build."""
        try:
            from PySide6.QtCore import QTimer
        except ImportError:  # Nuke releases before the PySide6 migration.
            from PySide2.QtCore import QTimer

        return QTimer()

    def _pump_host_queue(self, *_args: Any, **_kwargs: Any) -> None:
        """Drain a bounded amount of queued work during Nuke UI updates."""
        self._host_dispatcher.tick(16)

    def dispatch_callable(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("context", None)
        kwargs.pop("action_name", None)
        kwargs.pop("skill_name", None)
        kwargs.pop("execution", None)
        kwargs.pop("timeout_hint_secs", None)
        kwargs.pop("affinity", kwargs.pop("thread_affinity", None))
        if threading.current_thread() is threading.main_thread():
            return func(*args, **kwargs)
        import nuke  # Lazy import: requires a running Nuke process.

        return nuke.executeInMainThreadWithResult(func, args, kwargs)
