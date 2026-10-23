---
name: nuke-input-health
description: Inspect active Nuke input health before rendering, handing off or archiving a script. Find missing files, sequence gaps, range mismatches, suspicious frame sizes and unavailable required node classes.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke broken reads missing frames sequence gaps input health zero byte plugins range mismatch"
    tags: "nuke, compositing, inputs, diagnostics"
    tools: tools.yaml
---

# Nuke Input Health

Run `nuke_input_health__check_input_health` before rendering or packaging.
The tool inspects the active script recursively without editing it. Reports
are grouped by severity and frame lists are compressed into inclusive ranges.
Sequence checks cover the union of Root and Read ranges, plus limited Write
ranges when requested. Static `hold`, `loop` and `bounce` modes extend coverage
at their respective Read boundaries; those frames do not require additional
files outside the source range. Gaps inside the source range are still checked.
Unknown or animated boundary modes remain conservative. Frame offsets and
time remapping are not simulated; coverage is not a prediction of rendered pixels.
Read coverage is compared separately with Root and requested Write ranges.
`max_references` also caps the returned limited Write ranges; total counts and
an incomplete-check warning identify omissions. Frame unions are bounded before
their frame sets are allocated.

Provide `required_node_classes` from a handoff manifest to check classes that
could not be instantiated on this machine. Existing nodes prove their classes
are available; unknown nodes and absent required plug-ins are reported. A
missing inventory is reported as unverified. This cannot infer dependencies
of an unopened script or prove third-party license availability.

Increase the explicit limits if the result reports incomplete checks.
Animated file paths are checked at the current frame only. Very small frames
are a heuristic based on adjacent file sizes and need human inspection.
