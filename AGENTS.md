# AGENTS.md — dcc-mcp-nuke

> Nuke adapter for the DCC-MCP ecosystem: embeds a Streamable HTTP MCP server in Nuke and uses Nuke's main-thread execution API for scene tools. One package covers Nuke, NukeX, and Nuke Studio.
> Navigation map for AI agents, not a reference manual. Detailed API lives in `README.md`; install, verify, upgrade, and receipt-driven uninstall in `install.md`.

## Build & test

This repo has **no justfile** — use the commands CI runs directly:

```bash
python -m pip install -e ".[dev]"        # editable install with the dev extra
python -m pytest tests/test_install_lifecycle.py   # Install SOP contract smoke test
python -m pytest                          # full suite
ruff check src tests tools
ruff format --check src tests tools
python tools/lint_skills.py
python -m build && python -m twine check dist/*
python tools/verify_distribution.py dist --core-version 0.20.14
```

- **Python:** `>=3.9` (CI matrix 3.9–3.13; Windows/macOS exclude 3.9). Ruff target `py39`, line-length 120.
- Adapted for `dcc-mcp-core>=0.20.14,<0.21.0`. The upper bound is deliberate — Core `0.20.34` changed the Install SOP schema and made the module fail to import. **Do not widen the pin without running `tests/test_install_lifecycle.py`.**
- CI also runs a `core-latest compatibility` job against the newest published Core (outside the pin) so upcoming Core minors surface as warnings instead of silent breakage.

## Repo layout

| Path | Role |
|---|---|
| `src/dcc_mcp_nuke/` | Adapter package — `server.py`, `dispatcher.py`, `node_graph.py`, `compositing.py`, `gizmos.py`, `scripting.py`, `text_layout.py`, `host_flavor.py`, `_installer.py`, `install_cli.py`, `plugin.py`, `__version__.py` |
| `src/dcc_mcp_nuke/skills/` | 8 shipped skills (`nuke-diagnostics`, `-layered-compositing`, `-node-assets`, `-node-graph`, `-script`, `-scripting`, `-studio-timeline`, `-text-layout`); shipped as wheel artifacts |
| `src/dcc_mcp_nuke/nuke_plugin/` | In-host Nuke plug-in (`init.py`, `menu.py`); shipped as wheel artifacts |
| `tests/` | pytest suite (`testpaths = ["tests"]`, `pythonpath = ["src"]`) |
| `tools/` | `lint_skills.py`, `verify_distribution.py`, `release_integrity.py`, `skill_companion_audit.py` |
| `docs/` | Logo (`assets/`), images |

## Host flavors

Nuke, NukeX, and Nuke Studio ship from one installation, share one embedded Python interpreter and one `~/.nuke` plug-in profile, and install once. Core registers all three as executable stems of a **single `dcc-type` (`nuke`)** — there is no second package, entry point, or release channel.

What differs is the runtime feature surface. `host_flavor.py` classifies the running entry point and reports it as server capability metadata:

```bash
dcc-mcp-cli call nuke_diagnostics__host_flavor --dcc-type nuke --json '{}'
```

## Release

- release-please drives versioning from Conventional Commits on `main`.
- `feat:` → minor, `fix:` → patch, `chore:`/`docs:`/`ci:` → **no release**.
- Version is bumped in `pyproject.toml` (`$.project.version`) and `src/dcc_mcp_nuke/__version__.py`.
- `.github/workflows/release.yaml` builds hash-pinned artifacts and runs `tools/release_integrity.py` to bind the release to an immutable identity.
- Use `chore:`/`docs:` for config and doc work so release-please does not cut a valueless version.

## Do / Don't

- **Do** single-source agent instructions here. This is the only agent contract file at the repo root.
- **Do** prefer typed skills and tools over raw scripts, and drive the host through `dcc-mcp-cli` (`search` / `describe` / `call` / `load-skill`) rather than adapter-local Python.
- **Don't** add `CLAUDE.md` / `GEMINI.md` / `CURSOR.md` / `ANTHROPIC.md` / `OPENAI.md` / `COPILOT.md` / `CODEBUDDY.md` / `.cursorrules` / `.clinerules` / `.windsurfrules` at the root. Vendor-specific notes live under `docs/integrations/`, linked from here.
- **Don't** hardcode an exact version in tests (`assert __version__ == "X.Y.Z"`) — release-please bumps will break it. Use `>=` or read package metadata.
- **Don't** commit build artifacts to the repo root (`dist/`, `build/`, `*.egg-info`).
