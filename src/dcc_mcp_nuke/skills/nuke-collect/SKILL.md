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
The job runs on a worker. Only native SDK reference/dependency snapshots and
temporary script serialization use Nuke's main-thread bridge. Directory scans,
file checks, copies, archive creation and receipt verification run off that
thread, including dry runs. Nuke's own `fileDependencies` call still runs on the
main thread. Used mode schedules one output and one frame per callback, with
scene guards before and after that query, so other queued events can run
between callbacks. A single native query is indivisible and may still block
for seconds on network media or expensive graphs; it cannot be interrupted or
given a hard responsiveness guarantee. Cancellation is checked before a queued
callback starts and after native work returns. The existing job status remains
authoritative; no live per-query progress percentage is reported.

`frame_mode: all` collects every existing frame of printf/hash sequences.
`frame_mode: used` uses Nuke's `fileDependencies` for the selected inclusive
frame range (Root range by default), including Nuke's time-shift and view
dependency calculation. It queries Write outputs when present, otherwise the
nodes themselves, and unions the per-frame results. All queries must complete
against the same scene, including dry runs. Full scene signatures are checked
at each query boundary; this adds graph/knob inspection work per frame.
Queries are capped at 100000, returned dependency rows at 100000 and returned
path entries at 1000000 across the job. Duplicate rows/path entries count toward
these work limits; `max_files` separately bounds distinct files. A work-limit
refusal reports `dependency_query_limit`; narrow the frame range before retrying.

Only an ordinary, readable static `Read` with an unambiguous current/all-context
path may be absent from every successful explicit-output query and appear in
`unused_references`, with reason `not_reported_by_outputs`. Its media is not
scanned or copied, and its path and graph remain unchanged in the copied script.
The report is relative to the queried outputs and inclusive frame range, all
views and Nuke's current proxy mode. It does not prove that the Read is unused
for every possible output or configuration. Nuke's SDK excludes precomps,
gizmos and external plug-in assets from its file dependency calculation; review
the existing runtime dependency and unrewritten-path reports before handoff.

Other sequences without a proven dependency result are rejected.
An unproven sequence fails with `node`, `class`, `knob`, `path`, `frame_range`
and a `reason` in the error context; failed native queries also name `query_node`.
Failed or malformed queries, empty reported path lists, mismatched paths,
ambiguous contexts and absence without explicit outputs never permit this
omission. Proven used paths are checked directly without globbing the whole
sequence directory. `all` mode remains the faster choice when native used-frame
queries are expensive and collecting all frames is acceptable.
Write destinations are changed
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
File/font knobs whose all-view/time serialization differs from the current
context are reported as `multiple_context_path` or `multiple_context_font` and
are left intact, so a single-view rewrite cannot drop another view's paths.
The comparison uses an explicit `nuke.OutputContext()` because Nuke versions
can return all views from the no-argument `toScript()` call.

All copies are staged before publication. ZIP publication is an exclusive
atomic link; directory publication reserves a new target and moves staged
contents with the manifest last as the completion receipt. Failed directory
publication cleans up only the reserved task-owned target. Existing paths are
never overwritten. `inspect_package` verifies the receipt, safe relative names,
script presence and each dependency's stored byte size; it does not execute
scripts or certify media contents.

Only knobs whose all-view and explicit current-context serializations agree
can enter the temporary rewrite transaction. They are restored from their exact
original serialization in `finally`, along with Root name and modified state; undo
recording is suppressed during this temporary transaction. The copied script
is serialized with Nuke's `scriptSaveToTemp`, which leaves the original disk
script untouched. The native copy is first serialized to an owned local temp
file, then copied to staging by the worker. Changes to Root/script/frame state,
node membership, serialized knobs, input links or plug-in search paths during
filesystem work reject publication before temporary edits. A mismatch or failed
rollback fails the job before publication.
To package a different `.nk`, open it in an isolated Nuke session (including
`nuke -t`) and run the same typed operation there.
