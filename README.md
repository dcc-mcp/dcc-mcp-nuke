# dcc-mcp-nuke

<p align="center">
  <img src="docs/assets/dcc-mcp-nuke.svg" alt="DCC-MCP · NUKE" width="600">
</p>

<!-- dcc-mcp-coverage-pointer:start -->
<!-- Generated from dcc-mcp-catalog.yml by scripts/generate_adapter_pointer.py in dcc-mcp/dcc-mcp-core. Do not edit by hand. -->
## Part of the DCC-MCP host matrix

**dcc-mcp-nuke** — Foundry Nuke adapter for DCC-MCP.

It is one of **47 host adapters** in the DCC-MCP catalog. Every adapter speaks the same
MCP protocol and builds on the same core runtime contract; each one exposes the tools
its own host needs on top of that.

- [All host adapters and install metadata](https://dcc-mcp.github.io/ecosystem)
- [Host matrix on the core README](https://github.com/dcc-mcp/dcc-mcp-core#readme)
- [Showcase](https://dcc-mcp.github.io/showcase)

This block is generated from the catalog entry in
[`dcc-mcp-catalog.yml`](https://github.com/dcc-mcp/dcc-mcp-core/blob/main/dcc-mcp-catalog.yml).
Re-run the generator after changing the catalog.
<!-- dcc-mcp-coverage-pointer:end -->

## Agent workflow

AI agents should use the shared gateway through `dcc-mcp-cli`; IDE users may
continue to use the MCP endpoint. Prefer typed skills and tools over raw scripts.

### Install or update the CLI

`dcc-mcp-cli` is the preferred control path for every shell-capable agent. If
it is missing, ask the user before installing the latest official release:

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-core/main/scripts/install-cli.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-core/main/scripts/install-cli.ps1 | iex"
```

Keep an official build current through the release manifest:

```bash
dcc-mcp-cli update check
dcc-mcp-cli update apply
```

`update apply` downloads and stages the latest CLI for the next launch. It
does not update a running `dcc-mcp-server`; update that server in its own
environment.

```bash
dcc-mcp-cli dcc-types
dcc-mcp-cli list
dcc-mcp-cli search --query "<task>" --dcc-type nuke
dcc-mcp-cli describe <tool-slug>
dcc-mcp-cli call <tool-slug> --json '{"key":"value"}'
```

`dcc-types` reports release-catalog support; `list` reports live sessions. If a
tool belongs to an inactive progressive skill, call `dcc-mcp-cli load-skill <skill-name> --dcc-type nuke` before retrying. For post-task improvement,
attach a stable session id with `--meta-json`, query `dcc-mcp-cli stats --range 24h --session-id <task-id>`, then pass the bounded evidence to the
`review_skill_improvement` prompt from `dcc-mcp-skills-creator`.


Nuke adapter for the DCC Model Context Protocol. It embeds a Streamable HTTP
MCP server in Nuke and uses Nuke's main-thread execution API for scene tools.

See [install.md](install.md) for the agent-first install, verify, upgrade, and
receipt-driven uninstall workflow on Windows, macOS, and Linux.

## Host flavors: Nuke, NukeX, and Nuke Studio

One package covers all three Foundry entry points. Nuke, NukeX, and Nuke Studio
ship from one installation, share one embedded Python interpreter and one
`~/.nuke` plug-in profile, and install once. Core registers all three as
executable stems of a single `dcc-type` (`nuke`), so there is no second
package, entry point, or release channel to install.

What differs between them is the feature surface available at runtime. The
adapter classifies the running entry point as one of three host flavors and
reports it as server capability metadata:

```bash
dcc-mcp-cli call nuke_diagnostics__host_flavor --dcc-type nuke --json '{}'
```

| Host flavor | Provides |
|---|---|
| `nuke` | Shared baseline: `compositing`, `node_graph`, `scripting` |
| `nukex` | The same shared baseline |
| `nukestudio` | Baseline plus `studio.timeline`, `studio.sequence`, `studio.project_bin`, `studio.conform`, `studio.track` |

Detection prefers `nuke.env`, then the executable name, and treats an
importable `hiero` module as a supporting signal rather than a decisive one.
Set `DCC_MCP_NUKE_HOST_FLAVOR` only to override detection for a host that
really runs that flavor; unrecognized values are ignored.

### Studio-only skills are gated, not silently unavailable

A bundled skill that needs the Studio surface declares it in its manifest:

```yaml
metadata:
  dcc-mcp:
    host-flavors: [nukestudio]
```

On a `nuke` or `nukex` session that skill stays discoverable, so an agent can
still see the capability exists, but it will not load.
`dcc-mcp-cli load-skill nuke-studio-timeline --dcc-type nuke` fails with an
explicit veto naming both the required flavor and the current one, and calling
the tool directly returns the same `capability_unavailable` error. A Studio
skill never fails silently.

`nuke-studio-timeline` is the first Studio skill. It inspects open projects,
sequences, track counts, and frame range through the Hiero surface, read-only,
and reports an explicit `studio_surface_unavailable` error when a Studio build
does not expose the expected API instead of skipping it.

Probe the host with `nuke_diagnostics__host_flavor` first when the entry point
is unknown.

## Automated Houdini AOV compositing

![Automated Nuke AOV rebuild from a Houdini solar-system render](docs/images/nuke-houdini-aov-compositing.gif)

This real Nuke session progressively isolates Albedo, Sun, Diffuse, Glossy, and
Emission passes, then merges them into the approved composite. The 35-layer,
114-channel EXR source was rendered from a solar-system scene built in Houdini with
[`dcc-mcp-houdini`](https://github.com/dcc-mcp/dcc-mcp-houdini); Nuke reads the
Houdini AOVs rather than bundled sample footage.

Nuke loads the lifecycle-managed plug-in path and asks the operating system for
an available instance port. Use `dcc-mcp-cli list` or the stable gateway at
`http://127.0.0.1:9765/mcp` to discover and connect to the running instance.
Set `DCC_MCP_NUKE_PORT` only when a fixed direct port is required.

The `nuke-layered-compositing` template adds a composite to the current graph
through checked node CRUD without deleting existing work. It preserves existing
node names, connections, knobs, and selection; collisions get new unique names.
It applies the manifest's Root settings and saves the combined script to the
explicit `script_path`. Failed builds remove only added nodes and restore Root
settings and selection. Read/Write colorspaces use the same enumerated-name
validation as `nuke-color`. Repeated builds add another branch.

The bundled `nuke-script` skill can open an existing absolute `.nk` path,
inspect bounded node topology and knob values, sample per-channel AOV
statistics, and explicitly save the current script. It also starts a fresh
script with `new_script` (guarding unsaved changes), pastes nodes from an
absolute `.nk` file additively with `paste_nodes` without clearing existing
nodes, and reads or sets Root frame range, fps, and format through
`get_root_settings` and `set_root_settings`; root-settings writes validate the
range and enumerate the format list, require a matching readback, and roll back
on mismatch. The `nuke-node-graph`
skill adds non-clearing node CRUD, exact input connections, and readback-
verified static knob edits while rejecting executable knobs. The
`nuke-text-layout` skill creates or updates one bounded
`Text2` label, maps requested pixel size through Nuke's effective
`global_font_scale`, and returns verified node position, text box, and
alignment readback with rollback on mismatch. It rejects arbitrary node
classes, Python, Tcl, scripts, expressions, callbacks, bracket/backslash/control
text, animated/keyed/expression-bearing required knobs, and UI input while
preserving ordinary Unicode labels. Dynamic-state probes must return their
documented boolean, curve-list, and non-negative key-count shapes; unsupported
or unobservable probe results fail closed before mutation. Scale, box, and
position readback must use finite non-boolean numeric values with exact bounded
shapes; malformed readback fails closed and rolls back.

The `nuke-color` skill inspects and sets Nuke color management through bounded
typed verbs. `get_color_management` reports the active Root color-management
mode, the current OCIO config and its enumerated preset list, the working space,
and a bounded enumerated colorspace list without changing the script. The
`set_ocio_config`, `set_read_colorspace`, `set_write_colorspace`, and
`set_display_transform` verbs accept only names drawn from the enumerated lists,
reject unknown values instead of silently storing them, and require a matching
readback with rollback on mismatch.

The `nuke-paths` skill discovers every `File_Knob` recursively, including file
references inside groups and gizmos, and reports raw/evaluated paths and file
or sequence existence. Its prefix remap, absolute/script-relative conversion,
and unique missing-file relink tools default to dry run, preserve expressions
and animated knobs, and verify each write with whole-operation rollback on
failure. Reference lists and folder searches are bounded; ambiguous relinks
remain unchanged. These host-injected helpers also work in headless `nuke -t`.

The `nuke-preview` skill renders one node/frame to a temporary RGB PNG with a
bounded longest edge, file reference, source bounding box, and sampled channel
statistics. Select an RGB layer or one exact channel for grayscale inspection;
optional OCIO display/view names use the active config's enumerated lists.
The tool restores frame, proxy mode, selection, dirty state, group context,
and undo recording, and removes its temporary graph. Release each returned
`preview_id` with `cleanup_preview` after reading the image. Up to eight
previews (8 MiB each) live until cleanup or normal host exit; raw output clips
to [0, 1] and does not claim parity with a custom Viewer process.

The `nuke-input-health` skill checks input files, bounded sequence gaps and
frame sizes, Root/Read/optional Write range coverage, and required plug-in
classes without editing the graph. Issues are grouped by severity with
compressed frame ranges; limit and inspection failures are explicit. Supply
required classes from a handoff manifest to check dependencies absent from the
active graph. Third-party license availability requires separate verification.

The `nuke-collect` skill previews or packages the active script into a new
directory or zip with bounded dependency copies, a relative-path script and a
manifest. It supports full sequences or Nuke's used-frame dependency query,
geometry/LUT/config file knobs, discoverable plug-in scripts and native fonts.
Missing files, expressions, unresolved OCIO LUTs/imports, and compiled runtime
requirements are reported explicitly. Live knob serialization, Root state and
the original disk script are preserved; the async copy checks cancellation and
never overwrites an existing target. `inspect_package` verifies the receipt and
stored file sizes before handoff.

Releases use a canonical tag and exact-main identity check, one digest-bound
wheel/sdist bundle, and a minimal-OIDC publisher in `release.yaml` and the
GitHub `pypi` environment. Immediately before publishing, the workflow force-refreshes
authoritative main and tag refs into an isolated namespace and repeats the
identity checks. CI obtains the Install SOP schema from the isolated minimum-
Core environment used for the installed-wheel smoke rather than its ambient
interpreter.

The `nuke-plugins` skill inspects ordered host search paths, available and loaded
plug-in files, registered classes, and explicitly named local task roots without
loading code or changing the graph. Required classes distinguish registration
from filename candidates and incomplete checks; ABI, licenses and rendering
remain separate verification steps. Use the existing node-graph and Gizmo tools
after selecting a trusted asset. The skill's source references explain where to
find Gizmos and how to review, pin, download and use them.

The `nuke-machine-learning` skill inspects and builds the six AIR workflows:
CopyCat, BigCat beta, Inference, Deblur, Upscale and CatFileCreator. Creation is
additive and starts no computation. CopyCat, BigCat and CatFileCreator require
a real NukeX or Studio session; BigCat is available from 16.1. Native knob
contracts are checked rather than inferred from UI labels. Inference, Deblur
and Upscale reuse node graph, preview and Write/render tools.

Explicit TorchScript conversion and CPU training use the shared async jobs
with a 1–120 second wall-clock ceiling and an owned matching NukeX subprocess.
Training requires paired inputs and 1–1000 epochs; no model downloads, output
overwrites or implicit training occur. The live graph is restored before
execution; partial artifacts/logs remain available on cancellation or failure.
See the [AIR workflow reference](src/dcc_mcp_nuke/skills/nuke-machine-learning/references/air-workflows.md)
for trusted model inputs, native names, licensing and supported limits.

The `nuke-node-assets` skill packages reusable, versioned Gizmos with an
explicit public knob interface, instantiates saved assets, and validates live
instances. Its registered tools use `DCC_MCP_NUKE_PLUGIN_ROOT`, stable ids and
versions, bounded typed knobs, and reject executable callbacks.

The `nuke-layered-compositing` skill supports ordered global and
Cryptomatte-scoped gain, saturation, edge-feather, and bounded albedo-fill
adjustments without changing pixels outside the selected material.
