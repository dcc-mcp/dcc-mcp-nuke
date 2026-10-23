---
name: nuke-diagnostics
description: >-
  Host readiness diagnostics for a live Nuke adapter. Use for a bounded,
  read-only main-thread ping, host flavor detection, and capability
  reporting across Nuke, NukeX, and Nuke Studio. Not for script execution or
  graph mutation.
license: MIT
compatibility: "Nuke 14+; Python 3.9+; dcc-mcp-core 0.20.8+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: infrastructure
    search-hint: "nuke diagnostics ping readiness main thread host version flavor nukex studio"
    tags: "nuke, diagnostics, read-only, host-flavor"
    tools: tools.yaml
---

# Nuke Diagnostics

Use `nuke_diagnostics__ping` to prove that the selected live adapter can enter
Nuke's main thread and read its host version. It does not mutate the current
script or expose a scripting fallback.

Use `nuke_diagnostics__host_flavor` to report which Nuke entry point is
running: `nuke`, `nukex`, or `nukestudio`. Legacy capability fields are declared
host domains, not the implemented tool inventory. `runtime_evidence` separately
observes callable attributes on already loaded modules without invoking them.
A module not loaded is unknown. API presence is not successful execution or a
license entitlement: `license_status` remains `unknown`, native verification is
`not_performed`, and the report never authorizes execution or widens load gates.

The supported entry points use one adapter distribution and `dcc_type` (`nuke`),
but distinct processes/versions can have different Python, Qt, startup and license
constraints. Standalone Hiero bootstrap is not yet supported. Select the intended
live instance, inspect the shipped tool schema and respect the host-flavor gate.

Both probes are read-only. Neither mutates the script nor starts a server.
