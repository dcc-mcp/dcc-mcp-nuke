from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token

from dcc_mcp_nuke.script_package_scene import PackageScriptError, on_host_thread


class SwallowingBridge:
    """Model Nuke executeInMain.py: callback exceptions print and become None."""

    def __init__(self, host):
        self.host = host
        self.swallowed = []

    def executeInMainThreadWithResult(self, callback):
        try:
            return self.host.submit(callback).result(timeout=3)
        except BaseException as exc:
            self.swallowed.append(type(exc))
            return None


@pytest.mark.parametrize("value", [None, {"native": "plain result"}, (False, None)])
def test_worker_receives_the_exact_callback_result_including_none(value):
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        bridge = SwallowingBridge(host)
        assert worker.submit(on_host_thread, bridge, lambda: value).result(timeout=3) is value
        assert bridge.swallowed == []


def test_package_error_crosses_swallowing_bridge_with_original_type_and_context():
    error = PackageScriptError(
        "Nuke scene changed since collection snapshot",
        "scene_changed_since_snapshot",
        node="Write1",
        knob="colorspace",
        frame_range=[101, 102],
    )

    def sdk_failure():
        raise error

    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        bridge = SwallowingBridge(host)
        with pytest.raises(PackageScriptError) as raised:
            worker.submit(on_host_thread, bridge, sdk_failure).result(timeout=3)
        assert raised.value is error
        assert raised.value.context == {
            "node": "Write1",
            "knob": "colorspace",
            "frame_range": [101, 102],
            "reason": "scene_changed_since_snapshot",
        }
        assert bridge.swallowed == []


@pytest.mark.parametrize("exception", [ValueError("native failure"), SystemExit(3)])
def test_callback_exceptions_keep_their_original_type_without_class_name_matching(exception):
    def failure():
        raise exception

    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        bridge = SwallowingBridge(host)
        with pytest.raises(type(exception)) as raised:
            worker.submit(on_host_thread, bridge, failure).result(timeout=3)
        assert raised.value is exception
        assert bridge.swallowed == []


def test_queued_request_cancellation_survives_an_unrelated_host_context():
    token = CancelToken()
    calls = []
    with ThreadPoolExecutor(max_workers=1) as host, ThreadPoolExecutor(max_workers=1) as worker:
        bridge = SwallowingBridge(host)

        def dispatch(callback):
            token.cancel()
            return bridge.executeInMainThreadWithResult(callback)

        def run():
            reset = set_cancel_token(token)
            try:
                return on_host_thread(
                    SimpleNamespace(executeInMainThreadWithResult=dispatch), lambda: calls.append(True)
                )
            finally:
                reset_cancel_token(reset)

        with pytest.raises(DccMcpCancelledError):
            worker.submit(run).result(timeout=3)
        assert calls == []
        assert bridge.swallowed == []


@pytest.mark.parametrize("mode", ["not_called", "lost_result", "wrong_marker", "wrong_flag", "wrong_error"])
def test_missing_or_malformed_dispatch_receipt_fails_closed(mode):
    calls = []

    def dispatch(callback):
        if mode == "not_called":
            return None
        receipt = callback()
        if mode == "lost_result":
            return None
        if mode == "wrong_marker":
            return (object(), True, None)
        if mode == "wrong_flag":
            return (receipt[0], "yes", None)
        return (receipt[0], False, "an exception type name is not an exception")

    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(PackageScriptError) as raised:
            worker.submit(
                on_host_thread,
                SimpleNamespace(executeInMainThreadWithResult=dispatch),
                lambda: calls.append(True),
            ).result(timeout=3)
    assert raised.value.context["reason"] == "snapshot_dispatch_invalid_result"
    assert calls == ([] if mode == "not_called" else [True])


def test_duplicate_callback_dispatch_is_rejected_without_repeating_the_callback():
    calls = []

    def dispatch(callback):
        callback()
        return callback()

    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(PackageScriptError) as raised:
            worker.submit(
                on_host_thread,
                SimpleNamespace(executeInMainThreadWithResult=dispatch),
                lambda: calls.append(True),
            ).result(timeout=3)
    assert raised.value.context["reason"] == "snapshot_dispatch_invalid_result"
    assert calls == [True]


def test_direct_main_thread_callback_does_not_use_the_bridge():
    assert threading.current_thread() is threading.main_thread()

    def dispatch(_callback):
        pytest.fail("main-thread call should execute directly")

    error = PackageScriptError("refused", "scene_changed_since_snapshot")
    with pytest.raises(PackageScriptError) as raised:
        on_host_thread(SimpleNamespace(executeInMainThreadWithResult=dispatch), lambda: (_ for _ in ()).throw(error))
    assert raised.value is error
