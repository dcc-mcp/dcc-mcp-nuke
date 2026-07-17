import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_module():
    path = (
        Path(__file__).parent.parent
        / "src"
        / "dcc_mcp_nuke"
        / "skills"
        / "nuke-script"
        / "scripts"
        / "sample_channel_statistics.py"
    )
    spec = importlib.util.spec_from_file_location("sample_channel_statistics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_samples_layer_statistics_and_reports_non_finite_values(monkeypatch):
    module = _load_module()
    node = MagicMock()
    node.name.return_value = "Beauty"
    node.Class.return_value = "Read"
    node.width.return_value = 4
    node.height.return_value = 2
    node.channels.return_value = ["combinedvolume.red", "combinedvolume.green", "combinedvolume.blue"]

    def sample(channel, x, _y, _dx, _dy, frame):
        assert frame == 600
        if channel.endswith("red"):
            return 0.0 if x < 2 else 2.0
        if channel.endswith("green"):
            return math.nan if x < 2 else math.inf
        return 0.0

    node.sample.side_effect = sample
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=node)
    nuke.frame = MagicMock(return_value=1)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(
        node_name="Beauty",
        channels=["combinedvolume"],
        frame=600,
        max_samples_per_channel=4,
    )

    assert result["context"]["sampling"] == {
        "policy": "uniform_filtered_tiles",
        "roi": {"x": 0, "y": 0, "width": 4, "height": 2},
        "grid": {"columns": 2, "rows": 2},
        "samples_per_channel": 4,
        "max_samples_per_channel": 4,
        "samples_every_pixel": False,
    }
    red, green, blue = result["context"]["statistics"]
    assert (red["minimum"], red["maximum"], red["mean"], red["all_zero"]) == (0.0, 2.0, 1.0, False)
    assert green == {
        "channel": "combinedvolume.green",
        "minimum": None,
        "maximum": None,
        "mean": None,
        "finite_count": 0,
        "nan_count": 2,
        "positive_infinity_count": 2,
        "negative_infinity_count": 0,
        "has_non_finite": True,
        "all_zero": False,
    }
    assert blue["all_zero"] is True


def test_rejects_missing_channel_without_sampling(monkeypatch):
    module = _load_module()
    node = MagicMock()
    node.channels.return_value = ["rgba.red"]
    nuke = ModuleType("nuke")
    nuke.toNode = MagicMock(return_value=node)
    monkeypatch.setitem(sys.modules, "nuke", nuke)

    result = module.main.__wrapped__(node_name="Beauty", channels=["combinedvolume"])

    assert result["success"] is False
    assert "unavailable" in result["error"]
    node.sample.assert_not_called()
