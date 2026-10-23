---
name: nuke-node-graph
description: >-
  Host skill - inspect and edit a Nuke node graph through bounded typed CRUD
  and native node auto-layout. Use for non-destructive node creation, exact
  topology changes, layout, and static knob readback. Not for Python execution,
  animated knobs, or rendering.
license: MIT
compatibility: "Nuke Python API; dcc-mcp-core 0.19+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke node graph auto layout arrange autoplace create delete connect input inspect knob topology compositing"
    tags: "nuke, compositing, nodes, graph"
    tools: tools.yaml
---

# Nuke Node Graph

Inspect with `nuke_script__list_nodes` before mutating. Create one node at a
time, connect exact input indices, and read back changed knobs. The surface
never clears the graph and rejects executable or sensitive knob classes.

Use `auto_layout_nodes` with 1–256 exact `full_name` values from the graph
listing. The tool calls native `Node.autoplace()` in the supplied order and
returns each requested node's previous and final x/y coordinates. All targets
must share one parent Group. Group nodes are moved as single nodes; their
contents are not laid out recursively. Backdrop targets, locked nodes or
ancestor Groups, ambiguous identities, and unsupported native state fail
before mutation. The guarded graph is limited to 1000 nodes and 64 connected
input slots per node; names and coordinates use the existing bounded graph
contract.

Only requested positions may change. Connections, graph membership, selection,
and unrequested positions are verified afterward; failed native calls or
readbacks restore original positions, links, selection, and script dirty state.
A rollback failure is reported explicitly and leaves the script marked dirty.
The current Group context is preserved. Native autoplace avoids overlaps; its
exact arrangement can vary by host version and is not promised to be idempotent.
It does not resize or fit Backdrops, save, render, or change node settings.

Save the script explicitly only after the graph assertions pass.
