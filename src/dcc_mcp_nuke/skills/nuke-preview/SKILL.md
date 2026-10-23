---
name: nuke-preview
description: >-
  Render a bounded preview of one Nuke image node at one frame, with source
  bounding box and sampled channel statistics. Use for inspecting an AOV,
  alpha/key edge, grade, or comp output. Returns a temporary PNG file reference;
  release it after inspection. Optional OCIO display/view is explicitly selected.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke image preview thumbnail node render frame AOV layer alpha RGB PNG display view statistics"
    tags: "nuke, compositing, preview, image, render"
    tools: tools.yaml
---

# Nuke Node Preview

Call `render_node_preview` with the exact node name. It renders a single frame
to an RGB PNG in the system temporary directory and returns `file_path`, a
`file_ref` with SHA-256, actual dimensions and bytes, and an opaque `preview_id`.
Use the file reference to inspect the image. Never save previews beside the
current script.

`max_size` defaults to 512 pixels on the longest displayed edge (16–1024).
Non-square source pixels are converted to square pixels. `channels` accepts an
RGB layer such as `rgba` or `diffuse`, or one exact channel such as `rgba.alpha`
or `depth.Z`, displayed as grayscale. RGB output does not composite alpha;
inspect the alpha channel separately when checking transparency. A layer with
non-RGB components must be inspected one exact channel at a time.

Without `display`/`view`, the PNG contains raw numeric RGB clipped to [0, 1].
This is useful for channel inspection, but is not a claim of Viewer parity.
Supply both names to apply an `OCIODisplay` transform from the active config;
invalid names or hosts without the required knobs fail. Viewer gain, gamma,
custom ViewerProcess plug-ins, overlays, and stereo view selection are outside
this operation. Statistics describe the source channels before resizing or
display conversion, using at most 256 filtered tiles per channel; min/max are
sampled estimates rather than exhaustive extrema. The bbox is the source bbox.

Temporary nodes are confined to a temporary Group in the source node's parent,
then removed on success or failure. Frame, proxy mode, selection, script dirty
state, group context and undo recording are restored. No script is saved.

Call `cleanup_preview` with the returned `preview_id` after reading the PNG.
References live until explicit cleanup or normal host exit. At most eight
previews of at most 8 MiB each may remain; at capacity, release one before
rendering another. Cleanup never accepts filesystem paths and cannot remove
unrelated files. Failed renders clean their temporary directory immediately.
