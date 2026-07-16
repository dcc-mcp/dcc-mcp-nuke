from __future__ import annotations

from typing import Optional

import pytest


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
