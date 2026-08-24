# Install dcc-mcp-nuke

This runbook installs, verifies, upgrades, and removes the DCC-MCP adapter for
Foundry Nuke. The adapter follows the
[DCC-MCP Adapter Install SOP v1](https://dcc-mcp.github.io/dcc-mcp-core/guide/adapter-install-sop).

## Requirements

- **Nuke:** Nuke, NukeX, or Nuke Studio 14.0 or newer.
- **Python:** the interpreter that matches the selected Nuke installation; an
  unrelated system Python cannot make the package importable inside Nuke.
- **dcc-mcp-core:** `>=0.20.8,<1.0.0` in that same interpreter.
- **Platforms:** Windows, macOS, and Linux. Nuke's own qualified operating
  systems and licensing requirements still apply.
- **Permissions:** write access to the selected Nuke plug-in profile, normally
  `%USERPROFILE%\.nuke` on Windows and `$HOME/.nuke` on macOS/Linux.

Install the wheel with the exact target interpreter before running the
lifecycle command:

```text
<nuke-python> -m pip install "dcc-mcp-nuke==0.13.2"
```

On macOS, Foundry ships Nuke's Python application inside the Nuke app bundle.
On Windows and Linux, pass the exact compatible interpreter configured for the
Nuke installation. Do not fall back to whichever `python` happens to be on
`PATH`.

## Supported versions

| Nuke | Embedded Python line | Adapter | dcc-mcp-core | Platforms |
|---|---|---|---|---|
| Nuke 14.x | Python 3.9 | 0.13.2+ | 0.20.8+ | Windows/macOS/Linux |
| Nuke 15.x | Python 3.10 | 0.13.2+ | 0.20.8+ | Windows/macOS/Linux |
| Nuke 16.x | Python 3.11 | 0.13.2+ | 0.20.8+ | Windows/macOS/Linux |
| Nuke 17.x | Python 3.13 | 0.13.2+ | 0.20.8+ | Windows/macOS/Linux |

The installer checks the selected Nuke version, target interpreter, adapter
version, Core floor, `NUKE_PATH`, profile, existing receipt, and partial state
before writing anything. A Python major/minor mismatch fails closed.

## Agent quick path

Planning is the default and does not mutate the profile:

```text
dcc-mcp-nuke install --dcc-path <nuke-executable> --python <nuke-python> --json
```

Review the JSON plan and its machine-executable `next_steps`, then execute it:

```text
dcc-mcp-nuke install --dcc-path <nuke-executable> --python <nuke-python> --json --yes
dcc-mcp-nuke status --dcc-path <nuke-executable> --python <nuke-python> --json
```

All verbs accept `--json`, `--yes`, `--dry-run`, `--dcc-path`, and `--python`.
Exit codes are `0` success/plan, `10` preflight, `20` acquisition, `30`
transaction, `40` verify-to-usable, and `50` a proven restart requirement.

## Manual path

The installer creates a dedicated `dcc-mcp-nuke` plug-in directory beneath
the selected Nuke profile and adds one bounded, marked block to the profile's
shared `init.py`. It preserves all user-owned content; it never replaces the
shared file wholesale. The managed block calls `nuke.pluginAddPath()` so Nuke
loads the adapter's own `init.py` and `menu.py`.

If `NUKE_PATH` is unset, the selected profile is the user's `.nuke` directory.
If `NUKE_PATH` is set, its first entry is used and recorded. Set
`DCC_MCP_NUKE_PROFILE` when an operator deliberately needs another exact
profile. Use `--dry-run --json` to inspect the resolved path before execution.

Every mutation stages the complete plug-in directory, preserves the previous
payload and receipt until commit, and rolls back both the plug-in and shared
startup file on failure. A successful transaction writes
`.dcc-mcp/receipts/nuke.json` under the selected profile.

## Verify

Start the selected Nuke GUI, wait for its plug-in startup, then run:

```text
dcc-mcp-nuke verify --dcc-path <nuke-executable> --python <nuke-python> --json
dcc-mcp-cli wait-ready --dcc-type nuke
dcc-mcp-cli call nuke_diagnostics__ping --dcc-type nuke --json '{}'
```

Verification checks the receipt, SHA-256 digests, shared `init.py` registration,
target-interpreter imports, bootstrap error log, one live registry entry, and
the read-only main-thread `nuke_diagnostics__ping` tool. A copied plug-in or
healthy transport alone does not produce `directly_usable: true`.

## Upgrade

Save work and close Nuke before replacing loaded adapter files:

```text
<nuke-python> -m pip install --upgrade "dcc-mcp-nuke"
dcc-mcp-nuke upgrade --dcc-path <nuke-executable> --python <nuke-python> --json
dcc-mcp-nuke upgrade --dcc-path <nuke-executable> --python <nuke-python> --json --yes
```

After restarting Nuke, rerun `verify`. If the replacement fails, the previous
receipted payload, managed registration, and receipt are restored.

## Uninstall

Plan first, then remove only files and the exact managed block named by the
receipt:

```text
dcc-mcp-nuke uninstall --dcc-path <nuke-executable> --python <nuke-python> --json
dcc-mcp-nuke uninstall --dcc-path <nuke-executable> --python <nuke-python> --json --yes
<nuke-python> -m pip uninstall dcc-mcp-nuke
```

Uninstall is idempotent. It refuses unreceipted or modified files and preserves
all unrelated content in the shared Nuke `init.py`.

## Troubleshooting

| Result | Meaning | Recovery |
|---|---|---|
| Exit `10`, host | Nuke is missing, ambiguous, or older than 14.0. | Pass the exact executable with `--dcc-path`. |
| Exit `10`, Python | The interpreter is missing, lacks this adapter/Core, or does not match Nuke's embedded Python line. | Install the wheel into the exact target interpreter and pass it with `--python`. |
| Exit `10`, partial | Plug-in files or a managed block exist without trustworthy receipt ownership. | Inspect `status --json`; preserve unknown files and repair ownership deliberately. |
| Exit `30` | Staging, receipt commit, shared-file update, or rollback failed. | Preserve the report and previous receipt; resolve permissions before retrying. |
| Exit `40`, bootstrap | Nuke loaded the hook but captured an import/startup exception. | Inspect `<profile>/.dcc-mcp/logs/*.host-errors.log`, fix the reported target environment, and restart Nuke. |
| Exit `40`, readiness | No unique live Nuke instance answered the typed ping. | Start the selected licensed Nuke GUI, wait for startup, then rerun `verify`. |
| Exit `50` | Core found a loaded/locked adapter artifact. | Save work, close the locking Nuke process, and repeat the exact command. |

The Core catalog handoff should use
`https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-nuke/main/install.md` as the
adapter `instructions_url`.
