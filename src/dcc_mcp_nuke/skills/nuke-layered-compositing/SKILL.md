---
name: nuke-layered-compositing
description: Build and render deterministic layered Nuke composites from explicit manifests.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.19+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: compositing
    search-hint: "nuke layered compositing read merge write render"
    tags: "nuke,compositing,layers,render"
    tools: tools.yaml
---

# Nuke Layered Compositing

Use `build_layered_comp` with layers ordered bottom-to-top, then render the
returned Write node with `render_write_node`. For a multilayer EXR, reuse its
path with a `channel` per pass and ordered `grade`, `material_gain`,
`material_saturation`, `material_edge_blur`, `material_albedo_fill`, or `blur`
adjustments. Declare
`required_layers` when missing AOVs must fail before save.
Each new layer connects to Nuke input A and the accumulated composite to input
B, so `minus` evaluates `A-B`.

`render_write_node` reads back the active Write filename from
`nuke.filename(node)` AFTER `nuke.execute` returns, which follows Root proxy
mode, keeps sequence placeholders, and captures a `beforeRender` callback that
changed the path; the `file` knob is not used as the output path. Callback
state is combined from BEFORE and AFTER execute, so a per-frame or
`afterRender` callback that was set before the render but cleared itself during
it (or an unreadable callback knob) cannot be ruled out: one final filename
cannot prove the whole output set and verification stays `unknown`. Nuke also
runs render callbacks registered process-wide with `nuke.add*` in `init.py` or
`menu.py`; these are separate from the Write-node knobs, run during
`execute()`, and cannot be enumerated from the host. Any script may register
and remove temporary global callbacks while `execute()` runs, so empty
before/after knob snapshots alone do not prove a stable output path set. The
wrapper reads the raw Write `file` knob before and after `execute()`: a change
that is not attributable to a single `beforeRender` path edit, or a baseline
that cannot be read, keeps verification `unknown` rather than verifying one
final file. The readback never executes or parses callback scripts; the render
invoked through `nuke.execute` still runs configured callbacks. Write frame
renumbering is resolved by arithmetic only
for the identity `expression` and integer `offset` mappings; `start at`
renumbers against the first frame of the clip/sequence, which cannot be proven
from the Write node alone, so it stays explicitly `unknown` rather than
verifying stale output-N files. The verifier also binds `expected_format` to
the Write `file_type` (with aliases) or a well-founded path extension: a
readable, empty `file_type` permits path inference, but an unreadable
`file_type` stays `unknown` because the real value could override the path
suffix; an unknown or contradictory format stays `unknown`, never unrestricted
magic-number sniffing. The readback is filesystem-only, never native Nuke
render acceptance.

The builder adds nodes through the same checked CRUD creation path as
`nuke-node-graph`. Existing nodes, names, connections, knob values, and selection
are preserved. Name collisions receive new unique names, returned in the result.
Each call adds another composite; repeated calls are not idempotent. To replace
the script, explicitly start a new script first.

The manifest's frame range, fps, and output format become the Root settings,
and `script_path` saves the combined graph, overwriting that explicitly requested
path. Read and Write colorspace names must appear in the host's enumerated lists.
On failure, only newly added nodes are removed; previous Root settings, script
name, dirty flag, and selection are restored. A rollback failure is reported and
leaves the script marked dirty for inspection.

Use `material_gain` or `material_saturation` to change only materials selected
from a declared Cryptomatte layer. Use `material_edge_blur` to unpremultiply the
selected material, feather its alpha with EdgeBlur, premultiply it again, and
key it over the untouched image. Material saturation and edge size must be
non-negative.

Use `material_albedo_fill` only as a named creative readability pass. It clamps
the declared albedo AOV to 0-1, scales it by a strength from 0 to 0.25, and adds
RGB only inside the selected Cryptomatte. Missing albedo or Cryptomatte layers
fail before save and roll back the added graph; the beauty alpha and non-RGB
AOVs remain unchanged.
