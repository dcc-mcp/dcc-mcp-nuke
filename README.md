# dcc-mcp-nuke

Nuke adapter for the DCC Model Context Protocol. It embeds a Streamable HTTP
MCP server in Nuke and uses Nuke's main-thread execution API for scene tools.

```bash
python -m pip install dcc-mcp-nuke
```

Add the installed package's `dcc_mcp_nuke/nuke_plugin` folder to `NUKE_PATH`.
Nuke loads its `init.py` and serves MCP at `http://127.0.0.1:8765/mcp`.

The bundled `nuke-script` skill inspects scripts and nodes and can explicitly
save the current script. Releases are published through `release.yaml` and the
GitHub `pypi` environment.
