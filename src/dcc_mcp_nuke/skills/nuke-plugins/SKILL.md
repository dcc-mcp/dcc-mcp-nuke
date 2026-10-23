---
name: nuke-plugins
description: >-
  Host skill - inspect the running Nuke plug-in environment without executing
  plug-ins. Discover ordered search paths, available and loaded files,
  registered node classes, explicit local task-root assets, and class readiness
  before using an installed third-party node or Gizmo. Also find the documented
  Nukepedia, Nuke Survival Toolkit (NST), and author-repository Gizmo search,
  download and use workflow through the agent's web/GitHub tools.
license: MIT
compatibility: "Nuke Python API; Python 3.9+; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke plugins third party gizmo discovery search download use Nukepedia NST Nuke Survival Toolkit Spin Pixelfudger installed available loaded registered search paths OFX local inventory readiness"
    tags: "nuke, plugins, gizmo, third-party, read-only, nukepedia, nst"
    tools: tools.yaml
    skill-reference-docs:
      - "references/*.md"
---

# Nuke Plug-in Readiness

Use `inspect_plugin_environment` before loading or creating a third-party node.
It reads Nuke's ordered `pluginPath()` and separate available, loaded, and
registered inventories. Pass `required_node_classes` for exact class checks,
`name_filter` for a literal case-insensitive substring, and optional absolute
`task_roots` to inspect local assets outside the active search path.

The result distinguishes:

- `registered`: Nuke reports the exact node class as registered.
- `discoverable_candidate`: a known plug-in filename stem matches; this is a
  candidate and does not prove that the file registers that class.
- `not_registered`: the inspected inventories do not expose the exact class or
  a matching file candidate; a compiled bundle may expose additional classes
  when explicitly loaded.
- `unverified`: an inventory or bounded scan was incomplete.

Every requirement keeps `runtime_validation: not_run`. Registration or a local
file does not prove ABI compatibility, license availability, successful node
creation, or rendered output. Native/OFX binaries require the matching host,
platform and vendor-supported build. Python/Tcl scripts and Gizmos can execute
code when loaded; inventory does not load them.

All lists are bounded and expose counts/truncation. `max_scan_entries` caps
each host inventory and the shared task-root scan. Task roots are scanned up
to four nested directory levels, without following links outside the root or
executing `init.py`/`menu.py`. An OFX bundle is reported as one directory asset.
Filters affect asset and class lists, never hide the ordered host search paths,
and do not affect required-class checks. Counts describe the inspected matches;
probe and scan status must also be checked before concluding an asset is absent.

Task-root scans report file existence, kind, byte size, and whether Nuke already
reports that path loaded. A task root is not activated by inspection. Use an
explicit task-owned root and a known, license-checked asset; keep commercial
plug-in sources and credentials out of repositories and collected packages.

For an already registered class, reuse the existing `nuke-node-graph` creation,
knob, connection and inspection workflow, then verify with `nuke-preview`.
For an explicitly selected trusted `.gizmo`, `nuke-node-assets` provides
`instantiate_gizmo`; it adds the containing search path and loads the asset.
That legacy path-based operation executes the asset and does not supply the
registered-asset hash/interface contract. Use `gizmo_instantiate` for assets
published under the isolated `DCC_MCP_NUKE_PLUGIN_ROOT` registry instead.

Consult the [community source and acquisition workflow](references/sources.md)
for author repositories, license/pin checks, and source-specific download/use guidance. Discovery, source review,
loading, node creation, and rendering remain separate evidence steps.
