---
name: nuke-node-assets
description: >-
  Package reusable Nuke node graphs as versioned Gizmos, instantiate saved
  Gizmos, and inspect their public knob interface and graph health.
license: MIT
compatibility: "Nuke 13+ Python API; dcc-mcp-core 0.19+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "1.0.0"
    layer: domain
    stage: compositing
    search-hint: "nuke gizmo group reusable node asset package expose knobs instantiate inspect validate version"
    tags: "nuke,compositing,gizmo,group,asset,reuse"
    tools: tools.yaml
---

# Nuke Node Assets

Use `package_gizmo` to collapse explicit nodes into a Group, expose selected
internal knobs, and save a versioned `.gizmo`. Use `instantiate_gizmo` to load
that asset and set its public knobs, then `inspect_gizmo` to verify its
manifest, controls, children, and Nuke error state.

Package a new version to a new filename. Instance-preserving graph upgrades are
not part of this skill yet.
