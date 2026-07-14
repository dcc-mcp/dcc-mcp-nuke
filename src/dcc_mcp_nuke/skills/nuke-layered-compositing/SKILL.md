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
returned Write node with `render_write_node`.
