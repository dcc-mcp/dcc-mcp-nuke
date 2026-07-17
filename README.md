# dcc-mcp-nuke

Nuke adapter for the DCC Model Context Protocol. It embeds a Streamable HTTP
MCP server in Nuke and uses Nuke's main-thread execution API for scene tools.

```bash
python -m pip install dcc-mcp-nuke
```

Add the installed package's `dcc_mcp_nuke/nuke_plugin` folder to `NUKE_PATH`.
Nuke loads its `init.py` and asks the operating system for an available instance
port. Use `dcc-mcp-cli list` or the stable gateway at
`http://127.0.0.1:9765/mcp` to discover and connect to the running instance.
Set `DCC_MCP_NUKE_PORT` only when a fixed direct port is required.

The bundled `nuke-script` skill can open an existing absolute `.nk` path,
inspect scripts and nodes, sample bounded per-channel AOV statistics, and
explicitly save the current script. Releases are published through
`release.yaml` and the GitHub `pypi` environment.

The `nuke-node-assets` skill packages reusable, versioned Gizmos with an
explicit public knob interface, instantiates saved assets, and validates live
instances. Its registered tools use `DCC_MCP_NUKE_PLUGIN_ROOT`, stable ids and
versions, bounded typed knobs, and reject executable callbacks.

The `nuke-layered-compositing` skill supports ordered global and
Cryptomatte-scoped gain, saturation, edge-feather, and bounded albedo-fill
adjustments without changing pixels outside the selected material.
