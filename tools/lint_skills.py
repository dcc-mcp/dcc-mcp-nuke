from pathlib import Path

from dcc_mcp_core import validate_skill

paths = [path for path in (Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills").iterdir() if path.is_dir()]
reports = [validate_skill(str(path)) for path in paths]
assert all(report.is_clean for report in reports), [report.issues for report in reports]
print(f"validated {len(reports)} bundled skills")
