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

`render_write_node` performs the normal render with `nuke.execute`, which still
runs every configured callback (Write-node scripts and process-wide `nuke.add*`
handlers registered in `init.py`/`menu.py`). It reads the active Write filename
from `nuke.filename(node)` AFTER `nuke.execute` returns (proxy-aware, sequence
placeholders preserved), but reports it as INFORMATIONAL context only: the
wrapper never claims to verify host-derived output. Nuke exposes no public API
to enumerate process-wide render callbacks, to observe a handler
registered/removed during `execute()`, or to obtain a receipt of the path(s)
actually written. A callback can repoint the Write `file` knob during the
render and restore the original template afterwards, leaving all four node
callback knobs empty and the raw `file` knob equal before and after
`execute()`. That change-and-restore case is observationally identical to a
clean render from every public-API observation, so endpoint comparison, empty
before/after knob snapshots and a single final filename cannot prove the
complete output path set. The readback therefore fails closed to `unknown` for
EVERY render, never a false `verified=true` on a stale or wrong file; this is
a host limitation, not a render failure. The readback never executes or parses
callback scripts. The genuinely provable, independent file verification
contract is `dcc_mcp_nuke.diagnostics.verify_render_output`, which inspects an
explicit, caller-supplied output path and is deliberately not used here to
bless a host-derived single filename. The readback is filesystem-only, never
native Nuke render acceptance.

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
