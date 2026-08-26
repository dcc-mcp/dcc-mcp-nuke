"""Fail-closed release identity and immutable artifact verification."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_CANONICAL_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_VALUE = re.compile(r'^\s*(name|version)\s*=\s*"([^"]+)"\s*$')


class ReleaseIntegrityError(RuntimeError):
    """Raised when release identity or artifact evidence is not exact."""


def _run_git(repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"git exited {completed.returncode}"
        raise ReleaseIntegrityError(detail)
    return completed


def _project_metadata(content: str) -> dict[str, str]:
    section = ""
    metadata: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "project":
            continue
        match = _PROJECT_VALUE.fullmatch(raw_line)
        if match:
            metadata[match.group(1)] = match.group(2)
    if not metadata.get("name") or not metadata.get("version"):
        raise ReleaseIntegrityError("pyproject.toml must declare exact project name and version strings")
    return metadata


def _canonical_version(tag: str) -> str:
    match = _CANONICAL_TAG.fullmatch(tag)
    if not match:
        raise ReleaseIntegrityError(f"Release tag {tag!r} is not canonical vMAJOR.MINOR.PATCH")
    return ".".join(match.groups())


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseIntegrityError("Release identity must be a JSON object")
    required = {"schema_version", "project", "tag", "version", "commit", "tag_object"}
    if set(value) != required or value.get("schema_version") != 1:
        raise ReleaseIntegrityError("Release identity fields or schema version are invalid")
    for field in ("project", "tag", "version"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ReleaseIntegrityError(f"Release identity {field} is invalid")
    for field in ("commit", "tag_object"):
        if not isinstance(value.get(field), str) or not _FULL_SHA.fullmatch(value[field]):
            raise ReleaseIntegrityError(f"Release identity {field} is not an exact commit SHA")
    if _canonical_version(value["tag"]) != value["version"]:
        raise ReleaseIntegrityError("Release tag and version do not match")
    return dict(value)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write deterministic JSON used as immutable release evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_identity(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseIntegrityError(f"Release identity is unreadable: {exc}") from exc
    return _require_identity(value)


def _assert_ancestor(repository: Path, commit: str, main_ref: str) -> None:
    completed = _run_git(repository, "merge-base", "--is-ancestor", commit, main_ref, check=False)
    if completed.returncode != 0:
        raise ReleaseIntegrityError(f"Release commit {commit} is not an ancestor of {main_ref}")


def _assert_clean_checkout(repository: Path) -> None:
    status = _run_git(repository, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip()
    if status:
        raise ReleaseIntegrityError("Release identity requires a clean checkout with no uncommitted source")


def _source_metadata(repository: Path, revision: str) -> dict[str, str]:
    content = _run_git(repository, "show", f"{revision}:pyproject.toml").stdout
    return _project_metadata(content)


def resolve_release_identity(repository: Path, tag: str, main_ref: str) -> dict[str, Any]:
    """Resolve one canonical tag to an exact, checked-out commit on main."""
    repository = repository.resolve()
    _assert_clean_checkout(repository)
    version = _canonical_version(tag)
    tag_ref = f"refs/tags/{tag}"
    tag_object = _run_git(repository, "rev-parse", "--verify", tag_ref).stdout.strip()
    commit = _run_git(repository, "rev-parse", "--verify", f"{tag_ref}^{{commit}}").stdout.strip()
    if not _FULL_SHA.fullmatch(tag_object) or not _FULL_SHA.fullmatch(commit):
        raise ReleaseIntegrityError("Git did not resolve the release to exact SHA-1 object ids")
    _assert_ancestor(repository, commit, main_ref)
    head = _run_git(repository, "rev-parse", "HEAD").stdout.strip()
    if head != commit:
        raise ReleaseIntegrityError(f"HEAD {head} does not match release commit {commit}")
    committed = _source_metadata(repository, commit)
    worktree = _project_metadata((repository / "pyproject.toml").read_text(encoding="utf-8"))
    if committed != worktree:
        raise ReleaseIntegrityError("Worktree project metadata does not match the release commit")
    if committed["version"] != version:
        raise ReleaseIntegrityError(
            f"Release tag version {version} does not match pyproject version {committed['version']}"
        )
    return {
        "schema_version": 1,
        "project": committed["name"],
        "tag": tag,
        "version": version,
        "commit": commit,
        "tag_object": tag_object,
    }


def verify_release_identity(
    repository: Path,
    identity: Mapping[str, Any],
    main_ref: str,
    tag_ref: Optional[str] = None,
) -> None:
    """Recheck tag object, peeled commit, HEAD, ancestry, and version."""
    repository = repository.resolve()
    _assert_clean_checkout(repository)
    expected = _require_identity(identity)
    tag_ref = tag_ref or f"refs/tags/{expected['tag']}"
    tag_object = _run_git(repository, "rev-parse", "--verify", tag_ref).stdout.strip()
    if tag_object != expected["tag_object"]:
        raise ReleaseIntegrityError("Release tag object changed after the immutable build")
    commit = _run_git(repository, "rev-parse", "--verify", f"{tag_ref}^{{commit}}").stdout.strip()
    if commit != expected["commit"]:
        raise ReleaseIntegrityError("Release tag peeled commit changed after the immutable build")
    head = _run_git(repository, "rev-parse", "HEAD").stdout.strip()
    if head != expected["commit"]:
        raise ReleaseIntegrityError(f"HEAD {head} does not match release commit {expected['commit']}")
    _assert_ancestor(repository, commit, main_ref)
    committed = _source_metadata(repository, commit)
    if committed["name"] != expected["project"] or committed["version"] != expected["version"]:
        raise ReleaseIntegrityError("Committed project metadata does not match the release identity")
    worktree = _project_metadata((repository / "pyproject.toml").read_text(encoding="utf-8"))
    if worktree != committed:
        raise ReleaseIntegrityError("Release worktree version or project name changed after checkout")


def _refresh_authoritative_release_refs(
    repository: Path,
    tag: str,
    remote: str,
    main_branch: str,
) -> tuple[str, str]:
    """Fetch authoritative refs into an isolated namespace without touching user refs."""
    _canonical_version(tag)
    if not remote or remote.startswith("-"):
        raise ReleaseIntegrityError("Release remote name is invalid")
    branch_check = _run_git(repository, "check-ref-format", "--branch", main_branch, check=False)
    if branch_check.returncode != 0:
        raise ReleaseIntegrityError("Release main branch name is invalid")
    namespace = "refs/dcc-mcp-release-integrity"
    main_ref = f"{namespace}/main"
    tag_ref = f"{namespace}/tags/{tag}"
    _run_git(
        repository,
        "fetch",
        "--no-tags",
        "--force",
        "--no-write-fetch-head",
        "--refmap=",
        remote,
        f"+refs/heads/{main_branch}:{main_ref}",
        f"+refs/tags/{tag}:{tag_ref}",
    )
    return main_ref, tag_ref


def verify_authoritative_release_identity(
    repository: Path,
    identity: Mapping[str, Any],
    remote: str,
    main_branch: str,
) -> None:
    """Refresh remote main/tag into isolated refs, then recheck the release identity."""
    expected = _require_identity(identity)
    main_ref, tag_ref = _refresh_authoritative_release_refs(
        repository.resolve(),
        expected["tag"],
        remote,
        main_branch,
    )
    verify_release_identity(repository, expected, main_ref, tag_ref)


def _distribution_files(dist_dir: Path, identity: Mapping[str, Any]) -> list[Path]:
    files = sorted(path for path in dist_dir.iterdir() if path.is_file()) if dist_dir.is_dir() else []
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseIntegrityError("Release dist must contain exactly one wheel and one source archive")
    prefix = f"{re.sub(r'[-_.]+', '_', str(identity['project']))}-{identity['version']}"
    if any(not path.name.startswith(prefix) for path in files):
        raise ReleaseIntegrityError("Distribution filenames do not match the release project and version")
    return files


def _zip_write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def create_release_bundle(dist_dir: Path, identity_path: Path, bundle_path: Path) -> str:
    """Create one deterministic bundle containing identity, manifest, wheel, and sdist."""
    identity = _load_identity(identity_path)
    distributions = _distribution_files(dist_dir, identity)
    identity_bytes = (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode()
    manifest = {
        "schema_version": 1,
        "identity_sha256": _sha256_bytes(identity_bytes),
        "files": [
            {
                "path": f"dist/{path.name}",
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in distributions
        ],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w") as archive:
        _zip_write(archive, "release-identity.json", identity_bytes)
        _zip_write(archive, "release-manifest.json", manifest_bytes)
        for path in distributions:
            _zip_write(archive, f"dist/{path.name}", path.read_bytes())
    return _sha256_file(bundle_path)


def verify_release_bundle(
    bundle_path: Path,
    expected_sha256: str,
    extract_dir: Path,
) -> dict[str, Any]:
    """Verify the outer handoff and inner manifest before extracting distributions."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ReleaseIntegrityError("Expected bundle SHA-256 is malformed")
    actual_sha256 = _sha256_file(bundle_path)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise ReleaseIntegrityError("Transferred release bundle SHA-256 does not match the immutable build")
    with zipfile.ZipFile(bundle_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ReleaseIntegrityError("Release bundle contains duplicate paths")
        try:
            identity_bytes = archive.read("release-identity.json")
            manifest_bytes = archive.read("release-manifest.json")
            identity = _require_identity(json.loads(identity_bytes))
            manifest = json.loads(manifest_bytes)
        except (KeyError, ValueError, TypeError) as exc:
            raise ReleaseIntegrityError(f"Release bundle metadata is invalid: {exc}") from exc
        if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "identity_sha256", "files"}:
            raise ReleaseIntegrityError("Release manifest fields are invalid")
        if manifest["schema_version"] != 1 or manifest["identity_sha256"] != _sha256_bytes(identity_bytes):
            raise ReleaseIntegrityError("Release manifest identity digest does not match")
        if not isinstance(manifest["files"], list):
            raise ReleaseIntegrityError("Release manifest file list is invalid")
        expected_names = {"release-identity.json", "release-manifest.json"}
        verified_files: list[tuple[str, bytes]] = []
        for item in manifest["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
                raise ReleaseIntegrityError("Release manifest file entry is invalid")
            path = item["path"]
            if not isinstance(path, str) or not re.fullmatch(r"dist/[^/\\]+", path):
                raise ReleaseIntegrityError("Release manifest path is unsafe")
            try:
                content = archive.read(path)
            except KeyError as exc:
                raise ReleaseIntegrityError(f"Release bundle is missing {path}") from exc
            if len(content) != item["size"] or _sha256_bytes(content) != item["sha256"]:
                raise ReleaseIntegrityError(f"Release artifact digest does not match for {path}")
            expected_names.add(path)
            verified_files.append((path, content))
        if set(names) != expected_names:
            raise ReleaseIntegrityError("Release bundle contains files outside the immutable manifest")
        _distribution_files_from_names([path for path, _content in verified_files], identity)

    if extract_dir.exists():
        raise ReleaseIntegrityError(f"Verified extraction directory already exists: {extract_dir}")
    extract_dir.mkdir(parents=True)
    write_json(extract_dir / "release-identity.json", identity)
    write_json(extract_dir / "release-manifest.json", manifest)
    for relative, content in verified_files:
        target = extract_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {"identity": identity, "manifest": manifest, "bundle_sha256": actual_sha256}


def _distribution_files_from_names(names: Sequence[str], identity: Mapping[str, Any]) -> None:
    basenames = [Path(name).name for name in names]
    wheels = [name for name in basenames if name.endswith(".whl")]
    sdists = [name for name in basenames if name.endswith(".tar.gz")]
    prefix = f"{re.sub(r'[-_.]+', '_', str(identity['project']))}-{identity['version']}"
    if len(basenames) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseIntegrityError("Release manifest must bind exactly one wheel and one source archive")
    if any(not name.startswith(prefix) for name in basenames):
        raise ReleaseIntegrityError("Release artifact names do not match the release identity")


def _append_outputs(path: Optional[str], values: Mapping[str, str]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--repository", type=Path, default=Path.cwd())
    resolve.add_argument("--tag", required=True)
    resolve.add_argument("--main-ref", default="origin/main")
    resolve.add_argument("--identity", type=Path, required=True)
    resolve.add_argument("--github-output")

    bundle = subparsers.add_parser("create-bundle")
    bundle.add_argument("--dist", type=Path, required=True)
    bundle.add_argument("--identity", type=Path, required=True)
    bundle.add_argument("--bundle", type=Path, required=True)
    bundle.add_argument("--github-output")

    verify = subparsers.add_parser("verify-bundle")
    verify.add_argument("--repository", type=Path, default=Path.cwd())
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--main-branch", default="main")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--expected-bundle-sha256", required=True)
    verify.add_argument("--extract", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "resolve":
            identity = resolve_release_identity(args.repository, args.tag, args.main_ref)
            write_json(args.identity, identity)
            _append_outputs(
                args.github_output,
                {key: str(identity[key]) for key in ("commit", "tag", "version", "tag_object")},
            )
        elif args.command == "create-bundle":
            digest = create_release_bundle(args.dist, args.identity, args.bundle)
            _append_outputs(args.github_output, {"bundle_sha256": digest})
        else:
            verified = verify_release_bundle(args.bundle, args.expected_bundle_sha256, args.extract)
            verify_authoritative_release_identity(
                args.repository,
                verified["identity"],
                args.remote,
                args.main_branch,
            )
    except (OSError, ReleaseIntegrityError, zipfile.BadZipFile) as exc:
        print(f"release integrity check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
