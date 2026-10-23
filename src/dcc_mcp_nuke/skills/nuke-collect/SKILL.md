---
name: nuke-collect
description: >-
  Host skill - collect the active Nuke script and its dependencies into a new
  folder or zip. Preview file counts and bytes, collect images/sequences,
  geometry, LUT/config files, discoverable gizmo/plug-in scripts and fonts,
  serialize relative paths, and report missing or non-portable dependencies.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke collect package archive zip script dependencies media sequence geometry LUT OCIO fonts gizmos handoff"
    tags: "nuke, compositing, package, collect, dependencies"
    tools: tools.yaml
---

# Nuke Script Collection

Call `package_script` with an absolute `output_path`. A `.zip` suffix selects a
ZIP64 archive; other paths select a directory. The parent folder must exist and
the target must not exist. `dry_run: true` is the default: it reports planned
files, dependency bytes, missing references, unrewritten paths, and runtime
requirements without changing the graph or creating output. Review this report
before repeating with `dry_run: false`.

The tool runs through the existing asynchronous job model. Poll the returned
job with `jobs_get_status` and read its final result; request cancellation through
the shared job API. File/byte/reference caps apply before copying, report arrays
have independent bounds, and copies check cooperative cancellation every 8 MB.
Nuke host calls retain main-thread affinity. Copies remain bounded but can keep
the host busy until the async job finishes or cancellation is observed.

`frame_mode: all` collects every existing frame of printf/hash sequences.
`frame_mode: used` uses Nuke's `fileDependencies` for the selected inclusive
frame range (Root range by default), including Nuke's time-shift and view
dependency calculation. It queries Write outputs when present, otherwise the
nodes themselves. Sequences without a proven dependency result are rejected.
Disconnected sequences may require `all` mode. Write destinations are changed
only in the copied script to a package-local `renders` directory.

File discovery reuses `nuke-paths` and includes Root file knobs for custom OCIO
configs. Nuke's derived, read-only Root `OCIOConfigPath` is reported as
`host_managed_ocio_config` and is never edited; custom config path knobs remain
collectable. The layout is `script.nk`, `manifest.json`, `README.txt`, and
`assets/<source-directory-id>/<filename>`. Preserving source basenames and
grouping files by source directory avoids common collisions and preserves
sequence tokens. The manifest lists complete bounded contents; returned arrays
may be truncated.

Discoverable `.gizmo`, `.gznc`, `.py`, and `.tcl` sources matching node classes
are included. Add the manifest's `plugin_directories` to `NUKE_PATH` before
opening the package in another Nuke process. Native Text2 font serialization
is inspected for a font filename and resolved through Nuke plug-in/font search
paths. Collected fonts use a relative basename and their asset directory is
listed in `plugin_directories`; FreeType filenames do not use File_Knob Tcl
substitutions. Unsupported font forms are explicitly reported. Compiled/OFX plug-ins
and unverified node classes require a compatible Nuke/plugin installation and
are listed as runtime dependencies. Transitive script imports are reported as
unresolved. A copied OCIO config is explicitly marked unresolved because its
dependent LUT search paths are not recursively collected.

Expression-bearing and animated references remain unchanged and are reported,
even when an evaluated source file can be included. A package with missing or
unrewritten references needs manual resolution before handoff; collection does
not certify full portability.

All copies are staged before publication. ZIP publication is an exclusive
atomic link; directory publication reserves a new target and moves staged
contents with the manifest last as the completion receipt. Failed directory
publication cleans up only the reserved task-owned target. Existing paths are
never overwritten. `inspect_package` verifies the receipt, safe relative names,
script presence and each dependency's stored byte size; it does not execute
scripts or certify media contents.

The live graph's rewritten knobs are restored from their exact original
serialization in `finally`, along with Root name and modified state; undo
recording is suppressed during this temporary transaction. The copied script
is serialized with Nuke's `scriptSaveToTemp`, which leaves the original disk
script untouched. A mismatch or failed rollback fails the job before publication.
To package a different `.nk`, open it in an isolated Nuke session (including
`nuke -t`) and run the same typed operation there.
