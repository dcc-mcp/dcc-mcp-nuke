---
name: nuke-studio-timeline
description: >-
  Host skill - Nuke Studio only. Inspect the active timeline, sequences, and
  project/sequence summaries through the Hiero surface. Gated to the nukestudio host flavor;
  not available in plain Nuke or NukeX.
license: MIT
compatibility: "Nuke Studio 14+; dcc-mcp-core 0.20.14+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: timeline
    host-flavors: [nukestudio]
    search-hint: "nuke studio timeline sequence track project inspection hiero read-only"
    tags: "nuke, nukestudio, hiero, timeline, sequence, inspection, read-only"
    tools: tools.yaml
---

# Nuke Studio Timeline

This skill is **Nuke Studio only**. It declares
`metadata.dcc-mcp.host-flavors: [nukestudio]`, so the adapter refuses to load
it on a `nuke` or `nukex` session: `dcc-mcp-cli load-skill
nuke-studio-timeline --dcc-type nuke` fails with an explicit
`capability_unavailable` style veto instead of registering tools whose APIs do
not exist. The skill stays discoverable so agents can see that the capability
exists and why it is unavailable.

`inspect_timeline` reports the Hiero timeline surface reachable from the live
session — open projects, sequences, track counts, and frame range. It is
read-only and never mutates the project. When the Hiero API shape differs from
what this skeleton expects, the tool reports the mismatch explicitly rather
than failing silently.

Probe the host first with `nuke_diagnostics__host_flavor` when the entry point
is unknown. That tool reports `nuke`, `nukex`, or `nukestudio` plus the
declared host domains and bounded runtime observations, not license approval or native verification.

This is the first Studio skill. Legacy `studio.*` labels describe expected host
API domains, not implemented operations. No typed timeline editing, bin mutation,
conform or export tool is supplied here. Standalone Hiero is not yet supported by
the adapter bootstrap. Do not use flavor overrides to bypass that limitation.
