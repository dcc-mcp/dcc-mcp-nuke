from __future__ import annotations

import sys
from typing import Any, Optional

import pytest

import dcc_mcp_nuke.server as server_module
from dcc_mcp_nuke.dispatcher import NukeDispatcher
from dcc_mcp_nuke.host_flavor import FLAVOR_NUKE


class DispatcherLifecycleError(RuntimeError):
    """Raised from an injected host-flavor setup failure."""


@pytest.fixture
def dispatcher_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count dispatcher starts/stops instead of pumping Nuke's Qt event loop."""
    calls = {"start": 0, "stop": 0}

    class SpiedDispatcher(NukeDispatcher):
        def start(self) -> None:  # noqa: D102 - test double
            calls["start"] += 1

        def stop(self) -> None:  # noqa: D102 - test double
            calls["stop"] += 1

    monkeypatch.setattr(server_module, "NukeDispatcher", SpiedDispatcher)
    monkeypatch.setattr(sys, "executable", "Nuke16.0")
    return calls


def _flavor_setup_failure(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """Make one step of the post-``super().__init__`` flavor setup raise."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise DispatcherLifecycleError(f"{target} failed")

    if target == "set_skill_load_transform":
        monkeypatch.setattr(server_module.NukeMcpServer, "set_skill_load_transform", boom)
    else:
        monkeypatch.setattr(server_module, target, boom)


@pytest.mark.parametrize(
    "target",
    ["detect_host_flavor", "HostFlavorGate", "set_skill_load_transform"],
)
def test_host_flavor_setup_failure_stops_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher_spy: dict[str, int],
    target: str,
) -> None:
    """A flavor-setup failure must release the already-started dispatcher.

    ``NukeMcpServer`` starts the dispatcher before ``super().__init__`` runs, so
    every setup step that can raise has to sit inside the ``try`` that stops it
    again. Without the stop, the dispatcher thread and its queue pump leak and
    the next adapter start hits a half-initialized host bridge.
    """
    _flavor_setup_failure(monkeypatch, target)

    with pytest.raises(DispatcherLifecycleError):
        server_module.NukeMcpServer(port=0)

    assert dispatcher_spy == {"start": 1, "stop": 1}


def test_successful_construction_leaves_the_dispatcher_running(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher_spy: dict[str, int],
) -> None:
    """The happy path keeps the previous behavior: no stop before ``stop()``."""
    server = server_module.NukeMcpServer(port=0)
    try:
        assert dispatcher_spy == {"start": 1, "stop": 0}
        assert server.host_flavor == FLAVOR_NUKE
        assert server._config.instance_metadata["host_flavor"] == FLAVOR_NUKE
    finally:
        server.stop()

    assert dispatcher_spy == {"start": 1, "stop": 1}


@pytest.mark.parametrize(("requested_port", "expected_port"), [(None, None), (0, 0)])
def test_start_server_delegates_port_resolution_to_core(
    monkeypatch: pytest.MonkeyPatch,
    requested_port: Optional[int],
    expected_port: Optional[int],
) -> None:
    import dcc_mcp_nuke.server as server_module

    captured = {}

    class FakeServer:
        is_running = False

        def __init__(self, port: Optional[int]) -> None:
            captured["port"] = port

        def register_builtin_actions(self) -> None:
            pass

        def start(self) -> None:
            self.is_running = True

        def stop(self) -> None:
            self.is_running = False

    monkeypatch.setenv("DCC_MCP_NUKE_PORT", "18765")
    monkeypatch.setattr(server_module, "_server", None)
    monkeypatch.setattr(server_module, "NukeMcpServer", FakeServer)

    result = server_module.start_server(port=requested_port)

    assert result.is_running
    assert captured["port"] == expected_port
