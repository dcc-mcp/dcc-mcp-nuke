from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

from dcc_mcp_nuke.dispatcher import NukeDispatcher


class FakeHostDispatcher:
    def __init__(self) -> None:
        self.ticks: list[int] = []
        self.shutdown_calls = 0

    def post(self, func):
        return func

    def tick(self, budget: int) -> None:
        self.ticks.append(budget)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks.remove(callback)


class FakeTimer:
    def __init__(self) -> None:
        self.timeout = FakeSignal()
        self.interval = None
        self.started = False

    def setInterval(self, interval: int) -> None:
        self.interval = interval

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class DeletedFakeTimer(FakeTimer):
    def stop(self) -> None:
        raise RuntimeError("Internal C++ object (PySide2.QtCore.QTimer) already deleted")


class FakeNuke:
    def __init__(self) -> None:
        self.main_thread_calls = []

    def executeInMainThreadWithResult(self, func, args, kwargs):
        self.main_thread_calls.append((func, args, kwargs))
        return func(*args, **kwargs)


def test_dispatcher_exposes_and_pumps_core_host_queue(monkeypatch):
    fake_nuke = FakeNuke()
    monkeypatch.setitem(sys.modules, "nuke", fake_nuke)
    host_dispatcher = FakeHostDispatcher()
    dispatcher = NukeDispatcher(host_dispatcher=host_dispatcher)
    timer = FakeTimer()
    monkeypatch.setattr(dispatcher, "_new_qt_timer", lambda: timer)

    dispatcher.start()
    assert dispatcher.host_dispatcher is host_dispatcher
    assert timer.interval == 16
    assert timer.started
    assert len(timer.timeout.callbacks) == 1

    timer.timeout.callbacks[0]()
    assert host_dispatcher.ticks == [16]

    dispatcher.stop()
    assert not timer.started
    assert timer.timeout.callbacks == []
    assert host_dispatcher.shutdown_calls == 1


def test_dispatcher_stop_tolerates_qt_teardown(monkeypatch):
    host_dispatcher = FakeHostDispatcher()
    dispatcher = NukeDispatcher(host_dispatcher=host_dispatcher)
    monkeypatch.setattr(dispatcher, "_new_qt_timer", DeletedFakeTimer)

    dispatcher.start()
    dispatcher.stop()
    dispatcher.stop()

    assert host_dispatcher.shutdown_calls == 2


def test_dispatcher_uses_nuke_main_thread_api_from_worker(monkeypatch):
    fake_nuke = FakeNuke()
    monkeypatch.setitem(sys.modules, "nuke", fake_nuke)
    dispatcher = NukeDispatcher(host_dispatcher=FakeHostDispatcher())
    monkeypatch.setattr(threading, "current_thread", lambda: SimpleNamespace(name="worker"))
    monkeypatch.setattr(threading, "main_thread", lambda: SimpleNamespace(name="main"))

    assert dispatcher.dispatch_callable(lambda value: value + 1, 4) == 5
    assert len(fake_nuke.main_thread_calls) == 1
