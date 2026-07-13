"""Nuke's native main-thread bridge."""

from __future__ import annotations

import threading
from typing import Any, Callable


class NukeDispatcher:
    """Route core skill calls into Nuke without managing a second queue."""

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
