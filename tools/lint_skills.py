from pathlib import Path

from dcc_mcp_core import validate_skill
from skill_companion_audit import audit_companion_references

skills_root = Path(__file__).parents[1] / "src" / "dcc_mcp_nuke" / "skills"
paths = [path for path in skills_root.iterdir() if path.is_dir()]
reports = [validate_skill(str(path)) for path in paths]
assert all(report.is_clean for report in reports), [report.issues for report in reports]
companion_issues = audit_companion_references(skills_root)
assert not companion_issues, companion_issues
print(f"validated {len(reports)} bundled skills")
