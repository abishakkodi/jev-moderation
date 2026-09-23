"""Validate release identity before building or publishing. No network or credentials."""

import argparse
import json
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path, tag: str | None = None) -> str:
    js = json.loads((root / "packages/typescript/package.json").read_text())
    py = tomllib.loads((root / "packages/python/pyproject.toml").read_text())["project"]
    version = js["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or py["version"] != version:
        raise ValueError("Both packages must have the same X.Y.Z version")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag must match both package versions")
    if js["name"] != "jev-moderation" or py["name"] != "jev-moderation":
        raise ValueError("Unexpected registry package name")
    if not js.get("license") or js["license"] != py.get("license"):
        raise ValueError("Choose matching license metadata before release")
    license_text = (root / "LICENSE").read_bytes()
    for directory in ["packages/typescript", "packages/python"]:
        if (root / directory / "LICENSE").read_bytes() != license_text:
            raise ValueError("Package licenses must match the repository license")
    if f"## {version} " not in (root / "CHANGELOG.md").read_text():
        raise ValueError("Missing changelog entry")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        print(check(ROOT, args.tag))
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(str(error)) from error
