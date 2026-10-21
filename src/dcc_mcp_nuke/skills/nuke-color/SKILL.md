---
name: nuke-color
description: >-
  Host skill - inspect and set Nuke color management through bounded typed
  verbs. Use to enumerate OCIO configs, colorspaces, displays, and views, and to
  set the OCIO config, Read/Write colorspaces, and OCIODisplay display/view from
  enumerated lists. Not for raw Python execution or rendering.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.19+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke color management OCIO config colorspace colorspaces display view view transform OCIODisplay read write set"
    tags: "nuke, compositing, color, OCIO, colorspace"
    tools: tools.yaml
---

# Nuke Color Management

Read first with `get_color_management` before writing. It reports the active
Root color-management mode, the current OCIO config and its enumerated preset
list, the working space, and a bounded enumerated colorspace list, without
changing the script.

Writes are set from enumerated lists and reject unknown names instead of
silently storing them:

- `set_ocio_config` selects the Root OCIO config from the enumerated preset
  list; it fails closed when the presets are not enumerable.
- `set_read_colorspace` and `set_write_colorspace` set one Read or Write node's
  `colorspace` knob from that node's own enumerated colorspace list and reject
  any name outside it.
- `set_display_transform` sets one `OCIODisplay` node's `display` and `view`
  from their enumerated lists. Views are display-dependent, so the requested
  display is applied and verified before the view list is consulted.

Every write requires a matching readback and rolls the knob back when the host
cannot prove the requested value; it never reports success on a mismatch. All
verbs are synchronous, main-thread-affine, and bounded. Save the script
separately only after the color assertions pass.
