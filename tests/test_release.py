import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("check_release")
registry = load("registry_status")


@pytest.fixture
def project(tmp_path):
    for folder in ["packages/typescript", "packages/python"]:
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "LICENSE").write_text("license text")
    (tmp_path / "LICENSE").write_text("license text")
    (tmp_path / "CHANGELOG.md").write_text("## 0.1.0 — early release\n")
    (tmp_path / "packages/typescript/package.json").write_text(
        json.dumps({"name": "jev-moderation", "version": "0.1.0", "license": "MIT"})
    )
    (tmp_path / "packages/python/pyproject.toml").write_text(
        '[project]\nname="jev-moderation"\nversion="0.1.0"\nlicense="MIT"\n'
    )
    return tmp_path


def test_tag_must_match_both_versions(project):
    assert release.check(project, "v0.1.0") == "0.1.0"
    with pytest.raises(ValueError):
        release.check(project, "v0.2.0")
    p = project / "packages/python/pyproject.toml"
    p.write_text(p.read_text().replace("0.1.0", "0.2.0"))
    with pytest.raises(ValueError):
        release.check(project, "v0.1.0")


def test_license_drift_prevents_release(project):
    (project / "packages/python/LICENSE").write_text("different terms")
    with pytest.raises(ValueError):
        release.check(project)


def test_npm_retry_requires_identical_bytes(tmp_path):
    artifact = tmp_path / "jev-moderation-0.1.0.tgz"
    artifact.write_bytes(b"tarball")
    digest = "sha512-" + base64.b64encode(hashlib.sha512(artifact.read_bytes()).digest()).decode()
    assert registry.matching("npm", "0.1.0", tmp_path, lambda _: {"dist": {"integrity": digest}})
    assert not registry.matching("npm", "0.1.0", tmp_path, lambda _: None)
    with pytest.raises(ValueError):
        registry.matching("npm", "0.1.0", tmp_path, lambda _: {"dist": {"integrity": "wrong"}})


def test_pypi_requires_both_artifacts_and_matching_bytes(tmp_path):
    filenames = ["jev_moderation-0.1.0-py3-none-any.whl", "jev_moderation-0.1.0.tar.gz"]
    records = []
    for name in filenames:
        (tmp_path / name).write_bytes(name.encode())
        records.append(
            {"filename": name, "digests": {"sha256": hashlib.sha256(name.encode()).hexdigest()}}
        )
    assert registry.matching("pypi", "0.1.0", tmp_path, lambda _: {"urls": records})
    with pytest.raises(ValueError):
        registry.matching("pypi", "0.1.0", tmp_path, lambda _: {"urls": records[:1]})


def test_eval_splits_are_disjoint_and_valid():
    from jev_moderation import community_policy, resolve_policy, validate_cases

    splits = []
    for name in ["tuning", "held-out"]:
        cases = [
            json.loads(line)
            for line in (ROOT / "evaluation" / f"{name}.jsonl").read_text().splitlines()
        ]
        validate_cases(cases, resolve_policy(community_policy()))
        splits.append({c["message"].strip().lower() for c in cases})
    assert not splits[0].intersection(splits[1])
