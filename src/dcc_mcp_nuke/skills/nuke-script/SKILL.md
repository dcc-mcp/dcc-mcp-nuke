---
name: nuke-script
description: >-
  Host skill - open, inspect, start, paste into, and explicitly save Nuke
  scripts, and read or set Root settings. Use when restoring or clearing a known
  .nk file, pasting nodes without losing existing ones, setting frame range/fps/
  format, checking bounded node topology and knob values, Read channels, sampled
  channel statistics or multipart parts, or saving a known path. Not for raw
  Python execution.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.19+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke script root frame range fps format new script paste nodes selected nodes topology knob values read channels AOV statistics min max mean zero NaN Inf multipart parts open inspect save nk"
    tags: "nuke, compositing, nodes, script"
    tools: tools.yaml
---

# Nuke Script

Use `nuke_script__open_script` to restore an existing absolute `.nk` path. The
tool verifies the active script path, Root frame range, and loaded node count
before reporting success. Opening replaces the current graph, so save any
unsaved work first or explicitly set `discard_unsaved_changes` to `true`.

Use `nuke_script__new_script` to clear the graph and start a fresh script,
optionally applying Root frame range, fps, and format in the same call. It
guards unsaved changes unless `discard_unsaved_changes` is `true`, and frame
bounds must be supplied together.

Use `nuke_script__paste_nodes` to add nodes from an absolute `.nk` node-paste
file on top of the current graph. It is additive: pre-existing nodes are
preserved and the operation fails closed if any would be lost.

Use `nuke_script__get_root_settings` to read Root frame range, fps, format, and
the bounded enumerated format list before writing. Use
`nuke_script__set_root_settings` to set one or more of those values; each write
requires a matching readback, rejects an invalid range or a format outside the
enumerated list, and rolls back on mismatch.

Use `nuke_script__sample_channel_statistics` for bounded diagnostics. Its
min/max/mean and `all_zero` values describe uniform filtered tile samples; set
the ROI and sample limit explicitly when small or sparse features matter.

Use `nuke_script__list_nodes` before and after graph edits to compare exact
node classes, connected input indices, and bounded non-executable knob values.
