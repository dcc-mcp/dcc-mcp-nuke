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
