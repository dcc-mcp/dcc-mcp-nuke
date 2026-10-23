# Finding, downloading, and using community Gizmos

Use this workflow when a requested tool is absent from the current Nuke host.
First inspect the local environment; an installed filename, a registered class,
and a node that actually loads and renders are separate results.

## Sources

| Source | Discover and acquire | Agent notes |
| --- | --- | --- |
| [Nukepedia](https://www.nukepedia.com/) | Search tool names or categories; open the individual tool's Description, Licence, and Download sections. A useful web query is `site:nukepedia.com/tools/gizmos/ <effect>`. | Record the author, supported Nuke versions/platforms, licence, and actual download link. Some downloads require an account. Stop at an authentication gate; do not invent a download URL or undocumented API. |
| [Nuke Survival Toolkit](https://github.com/CreativeLyons/NukeSurvivalToolkit_publicRelease) | Inspect the author's [release page](https://github.com/CreativeLyons/NukeSurvivalToolkit_publicRelease/releases) and [tool wiki](https://creativelyons.github.io/NukeSurvivalToolkit_Wiki/). Pin the release/commit before download. | The repository carries an MIT licence; inspect individual bundled tools and retain their credits/terms too. Its `init.py`/`menu.py` provide toolkit integration and execute code. Inspect them before adding the whole toolkit to Nuke. |
| [SPIN VFX Gizmos](https://github.com/SpinVFX/spin_nuke_gizmos) | The author repository exposes individual `.gizmo` files under `gizmos/spin_tools`; download a specific commit's raw file and licence. | Repository licence: GPL-3.0. Keep licence and attribution with the downloaded copy; community assets are not bundled into this adapter. |
| [Pixelfudger](https://pixelfudger.com/pixelfudger-suite) | Use the author site or its [Nukepedia listing](https://www.nukepedia.com/tools/gizmos/filter/pixelfudger/). The author site uses a pay-what-you-can checkout. | The author permits personal/commercial use with credits retained. An account, checkout, or payment choice requires the user's participation; do not treat a displayed zero price as a completed download. |
| [Foundry plug-in directory](https://www.foundry.com/products/nuke-family/plug-ins) | Follow the listed vendor's own documentation/download route for compiled NDK/OFX products. | Match Nuke version, operating system, architecture, runtime dependencies, and licence. A Gizmo loading successfully does not qualify a compiled plug-in. |

These are discovery routes, not a claim that every tool supports every Nuke
version. Re-read the selected tool's current author documentation before use.
No live third-party search API is bundled with this adapter; use the agent's
web/GitHub tools, then pass explicit local paths to Nuke tools.

## Acquire into a new isolated directory

Record source page, author, exact download URL, immutable revision or version,
SHA-256, licence, and compatibility claims. A hash calculated after download
records byte identity; it is not an upstream digital signature.

For a GitHub source, resolve the revision and inspect files without executing it:

```powershell
gh api repos/SpinVFX/spin_nuke_gizmos/commits/HEAD --jq .sha
gh api 'repos/SpinVFX/spin_nuke_gizmos/git/trees/418beb01b1543d6c7860c3d8f4841cb662dce547?recursive=1' --jq '.tree[] | select(.path | endswith(".gizmo")) | .path'
```

This pinned SPIN example was downloaded and RGB-render tested in Nuke 16.1v2:

```powershell
$gizmoRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'nuke-gizmos/spin-418beb01'
if (Test-Path -LiteralPath $gizmoRoot) { throw 'Choose a new task directory' }
New-Item -ItemType Directory -Path $gizmoRoot | Out-Null
$gizmoSource = 'https://raw.githubusercontent.com/SpinVFX/spin_nuke_gizmos/418beb01b1543d6c7860c3d8f4841cb662dce547'
Invoke-WebRequest -Uri ($gizmoSource + '/LICENSE') -OutFile (Join-Path $gizmoRoot 'LICENSE')
Invoke-WebRequest -Uri ($gizmoSource + '/README.md') -OutFile (Join-Path $gizmoRoot 'README.md')
Invoke-WebRequest -Uri ($gizmoSource + '/gizmos/spin_tools/Keying/Erode_Fine.gizmo') -OutFile (Join-Path $gizmoRoot 'Erode_Fine.gizmo')
$gizmoDigest = (Get-FileHash -LiteralPath (Join-Path $gizmoRoot 'Erode_Fine.gizmo') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($gizmoDigest -ne '34db524cf8bcd61993b9280fe9b3135607f7c036db698b132d1397599a7e004b') { throw 'Unexpected Gizmo bytes' }
```

Keep downloaded assets outside the adapter repository and the user's existing
`.nuke`. For archives, inspect members and extract into a new task directory;
reject paths escaping that directory and unexpected links or executables.
Do not run an archive's installer, `init.py`, or `menu.py` as a download step.

## Inspect, load, and verify

1. Read the downloaded source and licence. Inspect callbacks, Python/Tcl code,
   executable user knobs, external file references, and dependencies. Static
   inspection is not a safety certificate. `pluginAddPath` can execute an
   accompanying `init.py`; do not register an unreviewed toolkit root.
2. Load `nuke-plugins` and inspect the explicit local directory. Treat discoverable
   files, registered classes, and loaded files as the reported states; none
   alone proves ABI compatibility or a valid vendor licence.
3. For an authorized, reviewed single `.gizmo`, load `nuke-node-assets` and use
   its existing `instantiate_gizmo` tool with the absolute local `gizmo_path`
   and a unique `node_name`. This executes the Gizmo. It is distinct from the
   stricter manifest-based `gizmo_instantiate` API for adapter-managed assets.
4. Connect inputs using `nuke-node-graph.connect_input`; inspect exposed knobs
   before setting them. Reuse the existing typed graph tools for already
   registered third-party node classes.
5. Use `nuke-preview.render_node_preview` on a small known input and verify
   channel statistics/render output. For the example above, a Constant input
   with RGB `[0.25, 0.5, 0.75]` and default `size=0` retained those RGB values.
   This is a Nuke 16.1v2 sample qualification, not Nuke 17 qualification.
6. Clean temporary previews with `cleanup_preview`; preserve licence/credits and
   record the exact class, source hash, host version, and validation result.
   Persistence is a separate explicit installation step. Gizmos remain external
   dependencies of saved scripts unless intentionally converted or packaged.

See Foundry's [Gizmo access guide](https://learn.foundry.com/nuke/content/comp_environment/configuring_nuke/sourcing_gizmos.html)
and [version-specific plug-in loading guidance](https://support.foundry.com/hc/en-us/articles/360002448139-Q100465-How-to-load-plugins-for-different-versions-of-Nuke).
