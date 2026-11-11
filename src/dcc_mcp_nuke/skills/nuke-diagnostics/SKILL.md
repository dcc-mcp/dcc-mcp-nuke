---
name: nuke-diagnostics
description: >-
  Host readiness and install diagnostics for a live Nuke adapter. Use for a
  bounded, read-only main-thread ping, host flavor detection, capability
  reporting across Nuke, NukeX, and Nuke Studio, post-install dependency
  diagnosis, and execution-source/billing transparency. Not for script
  execution or graph mutation.
license: MIT
compatibility: "Nuke 14+; Python 3.9+; dcc-mcp-core 0.20.8+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: infrastructure
    search-hint: "nuke diagnostics ping readiness main thread host version flavor nukex studio install dependency billing"
    tags: "nuke, diagnostics, read-only, host-flavor, install-sop, billing-transparency"
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

Use `nuke_diagnostics__diagnose_dependencies` after following install.md but
still seeing missing nodes or modules. It distinguishes Nuke's embedded Python
from an external CPython, locates module specs without importing or executing
any module code, and inventories third-party plug-ins without loading them.
A locatable spec is reported as `discoverable` with `import_tested: false`,
never as `importable`/`verified`; dotted names and transitive dependencies that
cannot be checked without running parent-package code stay `unknown`. Its
`repair` hint is conditional: discoverability alone does not prove
importability, so if the import fails in Nuke's embedded interpreter the user
is told to check transitive dependencies and the host environment. It never
claims "No action needed" for an untested import. A miss by the standard
spec-locating finders also stays `unknown`: custom import hooks, cached finders
and host plug-in paths are never consulted, so a standard miss is not global
unavailability. A caller-supplied lookup keeps its own explicit certainty. A
missing Nuke version contract and third-party ComfyUI reports are never turned
into a causal claim. The report never installs, loads, or prints credentials.

Use `nuke_diagnostics__describe_execution_sources` to answer where work runs
and whether any cloud model, provider, pricing, API key or paid call is added.
The adapter executes locally in the live Nuke process, with an optional bounded
local NukeX subprocess for Foundry AIR CPU nodes. This repository declares no
cloud integration, no billing estimate, no BYO API key and no paid calls; those
belong to the surrounding MCP host/CLI and are explicitly unknown here. Native
host verification remains `not_performed`.

Output readback is wired through `nuke_layered_compositing__render_write_node`:
a returning `nuke.execute` is not proof. The verifier binds `expected_format`
to the Write `file_type` or a well-founded output path extension before any
file is accepted; an unknown or contradictory format stays `unknown` instead
of being read as "any format". Magic-number sniffing only confirms a candidate
format against that binding, never native format acceptance. For PNG the
verifier validates a strict, provable subset with the standard library: chunk
lengths and CRCs, legal IHDR fields, required chunk ordering and bounded IDAT
decompression.
Unknown critical chunks and illegal structures fail; legal features it cannot
prove (indexed color, interlacing, palettes, unknown ancillary chunks) stay
`unknown`, never `verified`. Formats with no decoder here are signature-only,
with `content_acceptance: "unknown"` and `verified: false`. Content fields are
group-scoped over the full requested frame set: `full_decode`/`verified`
require a decodable check for every required frame, so missing or empty
outputs never make the group content-verified. A frame set truncated by
`max_frames` or collapsed by a literal path is only partially checked, so the
group stays `unknown` too. Only a matching PNG within the byte budget has its
body read; signature-only formats read just their magic. A literal path reports
the final actually requested frame (`first + (count - 1) * step`), never the
upper bound. Spec-legal PNG dimensions above this validator's local decode
budget are a capability limit (`unknown`), not an illegal IHDR. A filesystem
readback is never native Nuke render acceptance.

All probes are read-only. None mutates the script, starts a server, executes
user module code, reads credentials, or makes a network or paid call.
