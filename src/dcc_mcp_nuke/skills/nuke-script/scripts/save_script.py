from pathlib import Path

from dcc_mcp_core.skill import skill_entry, skill_error, skill_success


@skill_entry
def main(path: str, **_kwargs):
    import nuke  # Lazy import: requires Nuke.

    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".nk":
        return skill_error("Nuke script path must end in .nk", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = nuke.scriptName()
    except RuntimeError:
        current = ""
    if current and Path(current).expanduser().resolve() == target:
        saved = nuke.scriptSaveAs(str(target), overwrite=1)
    else:
        saved = nuke.scriptSaveAs(str(target))
    if saved is False:
        return skill_error("Nuke script was not saved", str(target))
    return skill_success("Saved Nuke script", path=str(target))
