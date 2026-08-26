from __future__ import annotations

import hashlib
import importlib.util
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOOL = ROOT / "tools" / "release_integrity.py"


def _release_module():
    assert RELEASE_TOOL.is_file(), "the release identity verifier must be source controlled"
    spec = importlib.util.spec_from_file_location("release_integrity", RELEASE_TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repository: Path, version: str, message: str) -> str:
    (repository / "pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    _git(repository, "add", "pyproject.toml")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path, version: str = "1.2.3") -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    return repository, _commit(repository, version, "initial release")


@pytest.mark.parametrize("annotated", [False, True])
def test_resolve_release_identity_accepts_canonical_lightweight_and_annotated_tags(
    tmp_path: Path,
    annotated: bool,
) -> None:
    release = _release_module()
    repository, commit = _repository(tmp_path)
    tag_args = ["tag"]
    if annotated:
        tag_args.extend(["-a", "-m", "release"])
    _git(repository, *tag_args, "v1.2.3")

    identity = release.resolve_release_identity(repository, "v1.2.3", "main")

    assert identity["schema_version"] == 1
    assert identity["tag"] == "v1.2.3"
    assert identity["version"] == "1.2.3"
    assert identity["commit"] == commit
    assert len(identity["tag_object"]) == 40


@pytest.mark.parametrize(
    "tag",
    ["1.2.3", "v1.2", "v1.2.3.4", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-rc.1", "v1.2.3+local"],
)
def test_resolve_release_identity_rejects_noncanonical_tags(tmp_path: Path, tag: str) -> None:
    release = _release_module()
    repository, _commit_id = _repository(tmp_path)
    _git(repository, "tag", tag)

    with pytest.raises(release.ReleaseIntegrityError, match="canonical"):
        release.resolve_release_identity(repository, tag, "main")


def test_resolve_release_identity_rejects_a_commit_outside_main(tmp_path: Path) -> None:
    release = _release_module()
    repository, main_commit = _repository(tmp_path)
    _git(repository, "checkout", "--orphan", "untrusted")
    _git(repository, "rm", "-rf", ".")
    _commit(repository, "1.2.3", "untrusted release")
    _git(repository, "tag", "v1.2.3")

    with pytest.raises(release.ReleaseIntegrityError, match="ancestor"):
        release.resolve_release_identity(repository, "v1.2.3", main_commit)


def test_resolve_release_identity_rejects_tag_and_project_version_mismatch(tmp_path: Path) -> None:
    release = _release_module()
    repository, _commit_id = _repository(tmp_path, version="1.2.4")
    _git(repository, "tag", "v1.2.3")

    with pytest.raises(release.ReleaseIntegrityError, match="pyproject version"):
        release.resolve_release_identity(repository, "v1.2.3", "main")


def test_release_identity_rejects_uncommitted_source_even_when_version_matches(tmp_path: Path) -> None:
    release = _release_module()
    repository, _commit_id = _repository(tmp_path)
    _git(repository, "tag", "v1.2.3")
    (repository / "uncommitted.py").write_text("print('not in the release commit')\n", encoding="utf-8")

    with pytest.raises(release.ReleaseIntegrityError, match="clean checkout"):
        release.resolve_release_identity(repository, "v1.2.3", "main")


def test_final_recheck_rejects_a_moved_tag_wrong_head_or_changed_version(tmp_path: Path) -> None:
    release = _release_module()
    repository, first_commit = _repository(tmp_path)
    _git(repository, "tag", "v1.2.3")
    identity = release.resolve_release_identity(repository, "v1.2.3", "main")

    second_commit = _commit(repository, "1.2.4", "next release")
    with pytest.raises(release.ReleaseIntegrityError, match="HEAD"):
        release.verify_release_identity(repository, identity, "main")

    _git(repository, "checkout", "--detach", first_commit)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    with pytest.raises(release.ReleaseIntegrityError, match="clean checkout"):
        release.verify_release_identity(repository, identity, "main")

    _git(repository, "checkout", "--", "pyproject.toml")
    _git(repository, "checkout", "--detach", second_commit)
    _git(repository, "tag", "--force", "v1.2.3")
    _git(repository, "checkout", "--detach", first_commit)
    with pytest.raises(release.ReleaseIntegrityError, match="tag object"):
        release.verify_release_identity(repository, identity, "main")


def test_final_recheck_refreshes_remote_main_and_tag_before_publish(tmp_path: Path) -> None:
    release = _release_module()
    source, first_commit = _repository(tmp_path)
    _git(source, "tag", "v1.2.3")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "origin", "main", "refs/tags/v1.2.3")

    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", str(remote), str(checkout))
    _git(checkout, "checkout", "--detach", first_commit)
    identity = release.resolve_release_identity(checkout, "v1.2.3", "origin/main")
    _git(checkout, "fetch", "origin", "main")
    fetch_head_path = checkout / _git(checkout, "rev-parse", "--git-path", "FETCH_HEAD")
    protected_refs = {
        "head": _git(checkout, "rev-parse", "HEAD"),
        "tag": _git(checkout, "rev-parse", "refs/tags/v1.2.3"),
        "origin_main": _git(checkout, "rev-parse", "refs/remotes/origin/main"),
        "fetch_head": fetch_head_path.read_bytes(),
    }

    second_commit = _commit(source, "1.2.4", "move authoritative release refs")
    _git(source, "tag", "--force", "v1.2.3")
    _git(source, "push", "origin", "main", "+refs/tags/v1.2.3:refs/tags/v1.2.3")

    # The checkout's stale remote-tracking and tag refs still accept the old identity.
    release.verify_release_identity(checkout, identity, "origin/main")
    with pytest.raises(release.ReleaseIntegrityError, match="tag object changed"):
        release.verify_authoritative_release_identity(checkout, identity, "origin", "main")
    assert _git(checkout, "rev-parse", "HEAD") == protected_refs["head"]
    assert _git(checkout, "rev-parse", "refs/tags/v1.2.3") == protected_refs["tag"]
    assert _git(checkout, "rev-parse", "refs/remotes/origin/main") == protected_refs["origin_main"]
    assert fetch_head_path.read_bytes() == protected_refs["fetch_head"]
    assert _git(checkout, "rev-parse", "refs/dcc-mcp-release-integrity/main") == second_commit
    assert _git(checkout, "rev-parse", "refs/dcc-mcp-release-integrity/tags/v1.2.3") == second_commit


def test_release_bundle_round_trip_is_digest_bound_and_rejects_tampering(tmp_path: Path) -> None:
    release = _release_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "fixture-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "fixture-1.2.3.tar.gz").write_bytes(b"sdist")
    identity_path = tmp_path / "release-identity.json"
    release.write_json(
        identity_path,
        {
            "schema_version": 1,
            "project": "fixture",
            "tag": "v1.2.3",
            "version": "1.2.3",
            "commit": "1" * 40,
            "tag_object": "2" * 40,
        },
    )
    bundle = tmp_path / "release-bundle.zip"

    digest = release.create_release_bundle(dist, identity_path, bundle)
    extracted = tmp_path / "verified"
    verified = release.verify_release_bundle(bundle, digest, extracted)

    assert digest == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert verified["identity"]["commit"] == "1" * 40
    assert sorted(path.name for path in (extracted / "dist").iterdir()) == [
        "fixture-1.2.3-py3-none-any.whl",
        "fixture-1.2.3.tar.gz",
    ]

    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr("dist/injected.txt", "untrusted")
    with pytest.raises(release.ReleaseIntegrityError, match="bundle SHA-256"):
        release.verify_release_bundle(bundle, digest, tmp_path / "rejected")


def test_release_workflow_is_sha_pinned_least_privilege_and_digest_bound() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "release.yaml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    assert set(jobs) == {"release-please", "build", "publish"}
    assert jobs["release-please"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert "id-token" not in jobs["build"]["permissions"]

    def named_step(job: dict[str, object], name: str) -> dict[str, object]:
        return next(step for step in job["steps"] if step.get("name") == name)

    def command(job: dict[str, object], name: str) -> list[str]:
        return shlex.split(str(named_step(job, name)["run"]), posix=True)

    build = jobs["build"]
    publish = jobs["publish"]
    assert build["needs"] == "release-please"
    assert build["if"] == "needs.release-please.outputs.release_created == 'true'"
    assert build["outputs"] == {
        "artifact_id": "${{ steps.upload.outputs.artifact-id }}",
        "bundle_sha256": "${{ steps.bundle.outputs.bundle_sha256 }}",
        "release_commit": "${{ steps.resolve.outputs.commit }}",
    }
    build_checkout = next(step for step in build["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert build_checkout["with"] == {
        "ref": "${{ needs.release-please.outputs.tag_name }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert named_step(build, "Resolve immutable release identity")["env"] == {
        "RELEASE_TAG": "${{ needs.release-please.outputs.tag_name }}"
    }
    assert command(build, "Resolve immutable release identity") == [
        "python",
        "tools/release_integrity.py",
        "resolve",
        "--tag",
        "$RELEASE_TAG",
        "--main-ref",
        "origin/main",
        "--identity",
        "$RUNNER_TEMP/release-identity.json",
        "--github-output",
        "$GITHUB_OUTPUT",
    ]
    assert command(build, "Install hash-pinned build inputs") == [
        "python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--require-hashes",
        "-r",
        ".github/release-requirements.txt",
    ]
    assert command(build, "Build wheel and source archive") == ["python", "-m", "build", "--no-isolation"]
    assert command(build, "Create digest-bound release bundle") == [
        "python",
        "tools/release_integrity.py",
        "create-bundle",
        "--dist",
        "dist",
        "--identity",
        "$RUNNER_TEMP/release-identity.json",
        "--bundle",
        "$RUNNER_TEMP/release-bundle.zip",
        "--github-output",
        "$GITHUB_OUTPUT",
    ]
    upload = named_step(build, "Upload one immutable handoff")
    assert upload["id"] == "upload"
    assert upload["with"]["path"] == "${{ runner.temp }}/release-bundle.zip"

    assert set(publish["needs"]) == {"release-please", "build"}
    publish_checkout = next(
        step for step in publish["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert publish_checkout["with"] == {
        "ref": "${{ needs.build.outputs.release_commit }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    download = named_step(publish, "Download the exact build artifact")
    assert download["with"] == {
        "artifact-ids": "${{ needs.build.outputs.artifact_id }}",
        "path": "${{ runner.temp }}/release-handoff",
    }
    assert command(publish, "Final source and artifact identity recheck") == [
        "python",
        "tools/release_integrity.py",
        "verify-bundle",
        "--remote",
        "origin",
        "--main-branch",
        "main",
        "--bundle",
        "$RUNNER_TEMP/release-handoff/release-bundle.zip",
        "--expected-bundle-sha256",
        "${{ needs.build.outputs.bundle_sha256 }}",
        "--extract",
        "$RUNNER_TEMP/verified-release",
    ]
    publisher = named_step(publish, "Publish verified distributions")
    assert publisher["with"]["packages-dir"] == "${{ runner.temp }}/verified-release/dist"

    action_uses = []
    for checked_workflow in ROOT.joinpath(".github", "workflows").glob("*.y*"):
        parsed = yaml.safe_load(checked_workflow.read_text(encoding="utf-8"))
        action_uses.extend(
            step["uses"] for job in parsed["jobs"].values() for step in job.get("steps", []) if "uses" in step
        )
    assert action_uses
    for action in action_uses:
        _name, separator, revision = action.rpartition("@")
        assert separator and len(revision) == 40 and all(character in "0123456789abcdef" for character in revision)

    requirements = ROOT.joinpath(".github", "release-requirements.txt").read_text(encoding="utf-8")
    requirement_lines = [line for line in requirements.splitlines() if line and not line.startswith("#")]
    assert requirement_lines
    assert all("==" in line and "--hash=sha256:" in line for line in requirement_lines)
