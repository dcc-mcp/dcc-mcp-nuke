---
name: nuke-paths
description: >-
  Host skill - discover every Nuke file reference and safely move media paths.
  Use to inspect recursive File_Knob references, remap storage prefixes, convert
  absolute and script-relative paths, or relink missing files by unique name.
  Changes default to dry run and preserve expressions and animated paths.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke file references paths missing media relink remap prefixes absolute relative geometry LUT groups"
    tags: "nuke, compositing, files, paths, relink"
    tools: tools.yaml
---

# Nuke File Paths

Start with `list_file_references`. It searches from Root recursively, including
groups and gizmos, and reports each retained `File_Knob` by full node name, class, knob,
raw value, evaluated path, existence, expression/animation state, and a bounded
error status. Evaluation uses Nuke's current output context and can evaluate
existing Tcl or Python expressions in the script. Existence means a concrete
file or at least one frame of a printf/hash sequence exists; it does not certify
all frames. Read, DeepRead, geometry, camera, LUT, and custom file knobs use the
same discovery path.

Empty knobs such as unused `icon` and Read `proxy` are omitted only when their
expression and keyed-animation state are known to be false. `reference_count`,
`max_items`, and truncation count the retained paths, including dynamic or
unreadable empty values. Exceeding the limit does not evaluate further paths.

All editing tools default to `dry_run: true`. Review the reported `new_value`
and status, repeat with `dry_run: false`, then inspect again. Edits verify each
knob's raw readback. Any failure restores and verifies every touched knob,
including the failing knob; failed rollback is reported explicitly. No tool
saves the script. A truncated reference scan refuses editing.

- `remap_path_prefixes` accepts up to 32 old/new absolute root pairs. Matching
  respects directory boundaries, is case sensitive, and chooses the longest
  matching prefix. Use `only_existing` to skip missing destinations and
  `node_classes` to restrict the affected classes.
- `convert_file_paths` with `mode: relative` converts only paths inside the
  saved script's folder to `[file dirname [value root.name]]/...`. With
  `mode: absolute`, only that exact generated expression is converted back.
  An unsaved script is rejected.
- `relink_missing_files` searches existing absolute folders recursively without
  following directory symlinks. Matching is case sensitive by file or sequence
  basename. Frames in one folder count as one sequence candidate; multiple
  candidate folders remain ambiguous and unchanged. Exceeding
  `max_search_files` refuses the operation so a partial search cannot appear
  unambiguous.

Other expression-bearing, animated, and unreadable knobs remain reported and
are left intact, including empty values with those states. Prefixes and replacement paths reject Tcl substitutions and
control characters. Lists, search work, and candidate reports have explicit
limits. The same host-injected Python helpers work in `nuke -t` without a GUI;
batch callers should open each `.nk`, preview, apply, inspect, then explicitly
save to a chosen destination.
