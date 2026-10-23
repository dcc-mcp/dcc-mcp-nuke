# Nuke family capability coverage

## Architecture and evidence

Keep one adapter repository/distribution/install entry point, with compositing,
editorial and UI modules selected by runtime context. A new Nuke Studio repository
would duplicate lifecycle, discovery, dispatch, version checks and overlapping
skills without solving API or license differences. Reconsider a split only with a
concrete incompatible distribution/dependency, independent owner/release or
licensing requirement that cannot be isolated in modules.

This matrix is a source inventory, not a native qualification report. No hosts were
launched to produce it. “Implemented” means shipped typed code exists; successful
real-host operation must be recorded per version, edition, GUI/headless mode and
license. Arbitrary Python access is not equivalent to typed coverage of all APIs.

| Domain | Current typed implementation | Next bounded delivery |
|---|---|---|
| Nuke graph | Create/delete nodes, exact input connections, static knob read/write, layout, groups/Gizmos | Inventory animation/expressions, channels/views, specialized node families separately; do not imply all knobs are writable |
| Script lifecycle | New/open/save/paste, Root settings, bounded inspection | Save-close-reopen fixture and state comparison across supported versions |
| Compositing | Layered AOV creation, color, image statistics, previews, input health | Dedicated typed tracking/roto/paint, 3D/deep, keying, animation and ML workflows only after API and license qualification |
| Render | Write-node execution and bounded preview | Job identity/status/progress/cancel/failure/output validation; existing `render_write_node` calls `nuke.execute` synchronously |
| Dependencies | File paths/remap/relink, package/collect, plug-in/Gizmo inspection | Reopen packaged artifacts and compare dependencies/output; do not reopen already delivered feature issues |
| Hiero inspection | Studio-only project/sequence summaries and track counts | Stable object identities, clips/media/bin trees, tracks/items, timecode/rates, selection and active context |
| Hiero project/media | No typed create/open/save/import/bin editing | Separate project lifecycle from media/bin CRUD; verify persistence, dirty state and undo |
| Timeline editing | No typed editing | Separate track/item CRUD and bounded trim/move/split; explicit overlap/ripple policy, audio/video links, retime and transitions |
| Conform | Declared host domain only | Import supported interchange, matching rules, dry-run conflicts, relink/reconform and result verification |
| Export | No typed editorial export | Preset discovery, output plan, submission then job state/cancel/recovery and output receipts |
| Studio bridge | No typed comp/timeline handoff | Versioned comp creation/update and linked-shot round trip; protect unrelated work |
| UI/review | No dedicated editorial UI control suite | Viewer/timeline selection/playhead/review, bounded screenshot evidence; CUA only for UI-only gaps, not a substitute for API verification |

“All Nuke and Hiero capabilities” is the long-term coverage target, not one
unbounded PR or a promise that every product feature has a public callable API.
Maintain an inventory against the supported release's official API and feature
list. For each item record typed support, scripting-only access, UI-only fallback,
license/mode restriction, or unsupported/unknown. Versioned fixtures, not labels,
are the completion criterion.

## Discovery and runtime isolation

Reuse existing `metadata.dcc-mcp.tags`, search hints and tool tags. Core gateway
search supports `dcc_type`, `instance_id`, AND `tags`, and OR `tags_any` on versions
that expose those fields; inspect the connected gateway schema before using an
optional filter. No new family-wide filter protocol is needed for this stage.
Search/discovery is not enforcement: host-flavor gating, API availability, running
mode and legal license rights must all remain separate checks.

Future execution eligibility should be the intersection of implemented operation,
compatible host version, required callable API, valid mode and applicable license.
An unknown license must never be reported as verified. Do not probe entitlement by
starting a render or loading a paid feature. Do not treat a UI workspace switch as
a license upgrade or a change of process. Keep the original `nuke` entry point and
existing callers compatible while adding explicit Hiero context when qualified.

Startup and dispatch require their own milestone: Nuke `init.py`/`menu.py` and
Hiero `Python/Startup`/`StartupUI` differ; Studio can execute both. Bootstrap must
be idempotent and must not create duplicate servers/timers. API imports must be
lazy and isolated so standalone Hiero does not assume a full Nuke compositing
surface. Queue mutations on the owning host's main thread; isolate background
I/O/jobs, cancellation and shutdown. Preserve Qt5/Qt6 and embedded Python version
boundaries, multiple live instances and unsupported/headless modes explicitly.

## Staged issue scopes and acceptance gates

These are proposed scopes, not newly posted issues. Recheck open work and assign
one owner before implementation. Keep feature PRs independently reviewable.

1. **P0: Evidence and discovery contract (this change).** Clarify one family,
   preserve existing gates, report non-authorizing loaded-API observations, remove
   unimplemented conform/editing search claims. Test missing/malformed modules and
   ensure diagnostics never invoke host APIs or claim licenses/native validation.
2. **P0: Native context/bootstrap qualification.** Explicit standalone Hiero
   detection, actual startup hooks, no double bootstrap, version/mode/edition
   matrix, main-thread heartbeat and shutdown. Do not enable it before real-host
   evidence. Coordinate any catalog changes with Core install tracking #2252/#2382.
3. **P1: Read-only editorial inventory.** Bounded project/bin/media/timeline
   traversal, stable IDs, frame/timecode/rate semantics, pagination and unavailable
   API reasons. Fixture: multiple projects, sequences, media and audio/video tracks.
4. **P1: Project lifecycle and media/bin authoring.** Separate PRs for guarded
   open/save/close and import/relink/bin CRUD. Fixture: create, mutate, save, close,
   reopen, compare state; cancellation/unsaved changes/missing media must be tested.
5. **P1: Timeline editing.** Separate PRs for track/item CRUD, edit operations and
   transitions/retiming/effects. Require preview/plan, explicit edit policy,
   transactional undo/readback and preservation of unrelated tracks.
6. **P1: Render/export job contract.** Share Core's existing job infrastructure
   rather than parallel async runtimes; separately integrate Nuke render and
   Hiero export. Test progress, cancel acknowledgement, terminal failure,
   interrupted connection, output completeness and safe recovery without duplicate
   submission. A timeout is not cancellation or success.
7. **P2: Conform and comp round trips.** Separate interchange import/reconform from
   Studio comp linking/versioning. Validate media matches, handles, timecode,
   frame ranges and save/reopen. Never silently accept ambiguous conform matches.
8. **P2: Advanced feature inventory and UI qualification.** Track Nuke specialty
   domains and remaining Hiero review/UI workflows as small scoped issues, with
   API versus CUA limitations and per-version evidence. Reuse Core verification
   #2269/#2261 and end-to-end benchmark #2270 instead of duplicating those systems.

Do not duplicate completed Nuke work: #50 graph/color, #80 OCIO (#86), #81 modal
open recovery (#88), #82 paths (#89), #83 collect (#92), #84 input health (#93),
#85 preview (#90), plus merged #96 layout and #97 plug-in inspection. New scopes
must identify a remaining gap rather than reopen the delivered feature wholesale.

## Official references

- [Hiero 17.0 Python guide](https://learn.foundry.com/nuke/developers/17.0/HieroPythonDevGuide/): core/UI modules, project/bin/media, undo/events, export, conform.
- [Hiero 17.0 core API](https://learn.foundry.com/nuke/developers/17.0/HieroPythonDevGuide/api/api_core.html): use exact-version signatures and mutation semantics. For example, adding an audio track item can cut/delete overlapping items, so a generic “add” wrapper is not inherently non-destructive.
- [Foundry startup scripts](https://support.foundry.com/hc/en-us/articles/360003811839-Q100490-What-are-the-init-py-and-menu-py-startup-script-files): Nuke, Hiero and Studio startup paths differ.
- [Nuke Indie restrictions](https://www.foundry.com/products/nuke-family/nuke-indie/faq): limited Python automation, encrypted project formats and external-render restrictions. Do not infer full support from an importable module or common installer.

References establish documented API/product boundaries, not native verification of
this adapter. Consult the installed release's matching docs before implementation.
