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
- Adapted for `dcc-mcp-core>=0.20.14,<0.21.0`. The upper bound is deliberate: it pins **below Core’s next minor**, so a new Core minor has to be promoted here on purpose rather than arriving unannounced.
- Core `0.20.34` is **inside** the range and works: `_installer.py` keeps Core’s Install SOP artifact revision (`INSTALL_SOP_SCHEMA_VERSION`, 2) separate from the report’s own `schema_version` const (1), which `tests/test_install_lifecycle.py` asserts.
- **Do not widen the pin without running `tests/test_install_lifecycle.py`.**
- CI also runs a `core-latest compatibility` job against the newest published Core (outside the pin) so upcoming Core minors surface as warnings instead of silent breakage.

## Repo layout

| Path | Role |
|---|---|
| `src/dcc_mcp_nuke/` | Adapter package — host/server dispatch, node graph and layout, compositing, `gizmos.py`, `plugins.py`, file references, previews, input health, script packaging, scripting, text layout, host flavors and installation |
| `src/dcc_mcp_nuke/skills/` | Shipped domain skills, including `nuke-node-graph`, `nuke-node-assets`, `nuke-plugins`, `nuke-paths`, `nuke-preview`, `nuke-input-health` and `nuke-collect`; each skill's `SKILL.md` and `tools.yaml` define its surface and ship in the wheel |
| `src/dcc_mcp_nuke/nuke_plugin/` | In-host Nuke plug-in (`init.py`, `menu.py`); shipped as wheel artifacts |
| `tests/` | pytest suite (`testpaths = ["tests"]`, `pythonpath = ["src"]`) |
| `tools/` | `lint_skills.py`, `verify_distribution.py`, `release_integrity.py`, `skill_companion_audit.py` |
| `docs/` | Logo (`assets/`), images |

## Host flavors

Use one distribution and `dcc-type` (`nuke`) for supported Nuke, NukeX and
Nuke Studio contexts. Do not split installation/release identity by workspace.
Standalone Hiero bootstrap remains unimplemented. Processes, host versions,
Python/Qt bindings and licenses must be qualified independently, not treated as
one shared interpreter or identical capability set.

`host_flavor.py` retains the existing flavor/load gate. Diagnostic `capabilities`
are legacy declared domains, not a complete implemented-tool list. The additive
`runtime_evidence` field only observes already loaded module attributes and never
grants execution or license rights. Consult shipped manifests and
[the capability matrix](docs/family-capabilities.md) before claiming coverage.

## Release

- release-please drives versioning from Conventional Commits on `main`.
- Whether a release is cut at all is a changelog question, not a prefix question: if every
  commit in the batch lands in a `hidden: true` section the changelog entry is empty, and
  release-please skips the whole batch — no release pull request, **no version bump**
  (`strategies/base.ts` logs “No user facing commits found since … - skipping” when
  `changelogEmpty()` finds only the heading line).
- For `release-type: python`: `chore:`/`ci:`/`style:`/`refactor:`/`test:`/`build:` are
  `hidden: true`; `docs:` is a **visible** `Documentation` section.
- Only once a release *is* cut does the prefix choose the bump: breaking → major,
  `feat:` → minor, anything else → patch
  (`DefaultVersioningStrategy.determineReleaseType()`).
- Use `chore:` when the batch should **not** cut a release; use `docs:` when doc-only work
  should cut a patch release.
- Version is bumped in `pyproject.toml` (`$.project.version`) and `src/dcc_mcp_nuke/__version__.py`.
- `.github/workflows/release.yaml` builds hash-pinned artifacts and runs `tools/release_integrity.py` to bind the release to an immutable identity.

## Do / Don't

- **Do** single-source agent instructions here. This is the only agent contract file at the repo root.
- **Do** prefer typed skills and tools over raw scripts, and drive the host through `dcc-mcp-cli` (`search` / `describe` / `call` / `load-skill`) rather than adapter-local Python.
- **Don't** add `CLAUDE.md` / `GEMINI.md` / `CURSOR.md` / `ANTHROPIC.md` / `OPENAI.md` / `COPILOT.md` / `CODEBUDDY.md` / `.cursorrules` / `.clinerules` / `.windsurfrules` at the root. This repo has no `docs/integrations/`; keep any vendor-specific notes here.
- **Don't** hardcode an exact version in tests (`assert __version__ == "X.Y.Z"`) — release-please bumps will break it. Use `>=` or read package metadata.
- **Don't** commit build artifacts to the repo root (`dist/`, `build/`, `*.egg-info`).
