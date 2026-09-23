"""Fail on version collisions; allow retry only when registry bytes match built artifacts."""

import argparse
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def matching(registry: str, version: str, directory: Path, fetch=None) -> bool:
    def fetch_json(url):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    fetch = fetch or fetch_json
    url = (
        f"https://registry.npmjs.org/jev-moderation/{version}"
        if registry == "npm"
        else f"https://pypi.org/pypi/jev-moderation/{version}/json"
    )
    data = fetch(url)
    if data is None:
        return False
    if registry == "npm":
        path = directory / f"jev-moderation-{version}.tgz"
        expected = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
        if data.get("dist", {}).get("integrity") != expected:
            raise ValueError("npm version exists with different bytes; refusing to publish or skip")
    else:
        hashes = {x["filename"]: x["digests"]["sha256"] for x in data.get("urls", [])}
        for filename in [
            f"jev_moderation-{version}-py3-none-any.whl",
            f"jev_moderation-{version}.tar.gz",
        ]:
            if (
                hashes.get(filename)
                != hashlib.sha256((directory / filename).read_bytes()).hexdigest()
            ):
                raise ValueError(
                    "PyPI version exists with different/missing files; inspect before retrying"
                )
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", choices=["npm", "pypi"])
    parser.add_argument("version")
    parser.add_argument("--artifacts", type=Path, default=Path("dist"))
    args = parser.parse_args()
    exists = matching(args.registry, args.version, args.artifacts)
    print("Identical version already published" if exists else "Version is not published")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"publish={str(not exists).lower()}\n")
