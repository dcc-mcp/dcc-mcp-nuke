import math

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success

MAX_CHANNELS = 16
MAX_SAMPLES_PER_CHANNEL = 4096


def _grid(width, height, sample_limit):
    if width * height <= sample_limit:
        return width, height
    columns = min(width, sample_limit, max(1, int(math.sqrt(sample_limit * width / height))))
    rows = min(height, max(1, sample_limit // columns))
    return columns, rows


def _resolve_channels(available, requested):
    resolved = []
    for name in requested:
        matches = [name] if name in available else [channel for channel in available if channel.startswith(name + ".")]
        if not matches:
            raise ValueError("channel or layer is unavailable: {}".format(name))
        for channel in matches:
            if channel not in resolved:
                resolved.append(channel)
    return resolved


def _validate(node_name, channels, frame, roi, sample_limit, zero_epsilon):
    if not isinstance(node_name, str) or not node_name.strip():
        raise ValueError("node_name must be a non-empty string")
    if (
        not isinstance(channels, list)
        or not 1 <= len(channels) <= MAX_CHANNELS
        or any(not isinstance(channel, str) or not channel.strip() for channel in channels)
    ):
        raise ValueError("channels must contain between 1 and {} non-empty names".format(MAX_CHANNELS))
    if frame is not None and (not isinstance(frame, int) or isinstance(frame, bool)):
        raise ValueError("frame must be an integer")
    if (
        not isinstance(sample_limit, int)
        or isinstance(sample_limit, bool)
        or not 1 <= sample_limit <= MAX_SAMPLES_PER_CHANNEL
    ):
        raise ValueError("max_samples_per_channel must be between 1 and {}".format(MAX_SAMPLES_PER_CHANNEL))
    if (
        isinstance(zero_epsilon, bool)
        or not isinstance(zero_epsilon, (int, float))
        or not math.isfinite(zero_epsilon)
        or zero_epsilon < 0
    ):
        raise ValueError("zero_epsilon must be a finite non-negative number")
    if roi is not None:
        if not isinstance(roi, dict) or set(roi) != {"x", "y", "width", "height"}:
            raise ValueError("roi must contain exactly x, y, width, and height")
        if any(not isinstance(roi[key], int) or isinstance(roi[key], bool) for key in roi):
            raise ValueError("roi values must be integers")
        if roi["width"] <= 0 or roi["height"] <= 0:
            raise ValueError("roi width and height must be positive")


@skill_entry
def main(
    node_name,
    channels,
    frame=None,
    roi=None,
    max_samples_per_channel=1024,
    zero_epsilon=0.0,
    **_kwargs,
):
    try:
        _validate(node_name, channels, frame, roi, max_samples_per_channel, zero_epsilon)
    except ValueError as exc:
        return skill_error("Invalid channel statistics request", str(exc))

    import nuke  # Lazy import: requires Nuke.

    node = nuke.toNode(node_name)
    if node is None:
        return skill_error("Failed to sample channel statistics", "node not found")

    available = list(node.channels())
    try:
        resolved = _resolve_channels(available, channels)
    except ValueError as exc:
        return skill_error("Failed to sample channel statistics", str(exc))
    if len(resolved) > MAX_CHANNELS:
        return skill_error(
            "Failed to sample channel statistics",
            "requested layers expand to more than {} channels".format(MAX_CHANNELS),
        )

    used_roi = (
        dict(roi) if roi is not None else {"x": 0, "y": 0, "width": int(node.width()), "height": int(node.height())}
    )
    if used_roi["width"] <= 0 or used_roi["height"] <= 0:
        return skill_error("Failed to sample channel statistics", "node image dimensions must be positive")

    used_frame = int(nuke.frame()) if frame is None else frame
    columns, rows = _grid(used_roi["width"], used_roi["height"], max_samples_per_channel)
    cell_width = used_roi["width"] / columns
    cell_height = used_roi["height"] / rows
    points = [
        (
            used_roi["x"] + (column + 0.5) * cell_width,
            used_roi["y"] + (row + 0.5) * cell_height,
        )
        for row in range(rows)
        for column in range(columns)
    ]

    statistics = []
    for channel in resolved:
        try:
            values = [float(node.sample(channel, x, y, cell_width, cell_height, used_frame)) for x, y in points]
        except (TypeError, ValueError, RuntimeError) as exc:
            return skill_error("Failed to sample channel statistics", "{}: {}".format(channel, exc))

        finite = [value for value in values if math.isfinite(value)]
        nan_count = sum(math.isnan(value) for value in values)
        positive_infinity_count = sum(value == math.inf for value in values)
        negative_infinity_count = sum(value == -math.inf for value in values)
        all_finite = len(finite) == len(values)
        statistics.append(
            {
                "channel": channel,
                "minimum": min(finite) if finite else None,
                "maximum": max(finite) if finite else None,
                "mean": math.fsum(finite) / len(finite) if finite else None,
                "finite_count": len(finite),
                "nan_count": nan_count,
                "positive_infinity_count": positive_infinity_count,
                "negative_infinity_count": negative_infinity_count,
                "has_non_finite": not all_finite,
                "all_zero": all_finite and all(abs(value) <= zero_epsilon for value in finite),
            }
        )

    return skill_success(
        "Sampled Nuke channel statistics",
        node_name=node.name(),
        node_class=node.Class(),
        frame=used_frame,
        requested_channels=channels,
        resolved_channels=resolved,
        zero_epsilon=float(zero_epsilon),
        sampling={
            "policy": "uniform_filtered_tiles",
            "roi": used_roi,
            "grid": {"columns": columns, "rows": rows},
            "samples_per_channel": len(points),
            "max_samples_per_channel": max_samples_per_channel,
            "samples_every_pixel": columns == used_roi["width"] and rows == used_roi["height"],
        },
        statistics=statistics,
    )
