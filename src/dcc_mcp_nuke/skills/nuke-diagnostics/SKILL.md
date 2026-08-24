---
name: nuke-diagnostics
description: >-
  Host readiness diagnostics for a live Nuke adapter. Use for a bounded,
  read-only main-thread ping before scene work. Not for script execution or
  graph mutation.
license: MIT
compatibility: "Nuke 14+; Python 3.9+; dcc-mcp-core 0.20.8+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: infrastructure
    search-hint: "nuke diagnostics ping readiness main thread host version"
    tags: "nuke, diagnostics, read-only"
    tools: tools.yaml
---

# Nuke Diagnostics

Use `nuke_diagnostics__ping` to prove that the selected live adapter can enter
Nuke's main thread and read its host version. It does not mutate the current
script or expose a scripting fallback.
