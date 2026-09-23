"""Install real distribution files (or registry versions) into clean consumer projects."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = {
    "schemaVersion": 1,
    "version": "consumer-v1",
    "mode": "replace",
    "filters": [{"id": "ads", "definition": "Unsolicited advertising."}],
}
JS = """
import { Moderator, MockClient, mockResponse, evaluate, type PolicyConfig } from 'jev-moderation';
const policy: PolicyConfig = {schemaVersion:1,version:'consumer-v1',mode:'replace',filters:[{id:'ads',definition:'Unsolicited advertising.'}]};
const moderator = new Moderator({policy,client:new MockClient([mockResponse({ads:.7})])});
if((await moderator.moderate('Example')).action !== 'review') throw new Error('Decision mismatch');
const report = await evaluate(policy,[{id:'clean',message:'Hi',expectedAction:'allow'}],{moderator:new Moderator({policy,client:new MockClient([mockResponse({ads:.1})])})});
if(!report.passed) throw new Error('Evaluation mismatch');
console.log('TypeScript installed SDK: imports, types, custom policy, and evaluation passed');
"""
PY = """
import asyncio
from jev_moderation import Moderator, AsyncModerator, MockClient, AsyncMockClient, mock_response
policy = {'schemaVersion':1,'version':'consumer-v1','mode':'replace','filters':[{'id':'ads','definition':'Unsolicited advertising.'}]}
with Moderator(policy=policy,client=MockClient([mock_response({'ads':.7})])) as m:
    assert m.moderate('Example')['action']=='review'
async def run():
    async with AsyncModerator(policy=policy,client=AsyncMockClient([mock_response({'ads':.1})])) as m:
        assert (await m.moderate('Example'))['action']=='allow'
asyncio.run(run())
print('Python installed SDK: sync and async imports and custom policies passed')
"""


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--artifacts", type=Path)
    group.add_argument("--registry-version")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cached npm dependencies and an optional pip wheelhouse",
    )
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    version = (
        args.registry_version
        or json.loads((ROOT / "packages/typescript/package.json").read_text())["version"]
    )
    if args.artifacts:
        npm_source = str((args.artifacts / f"jev-moderation-{version}.tgz").resolve())
        py_source = str((args.artifacts / f"jev_moderation-{version}-py3-none-any.whl").resolve())
        for path in [npm_source, py_source]:
            if not Path(path).is_file():
                parser.error(f"Missing artifact: {path}")
    else:
        npm_source, py_source = f"jev-moderation@{version}", f"jev-moderation=={version}"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in [
            "PYTHONPATH",
            "NODE_PATH",
            "TYPESAFE_API_KEY",
            "TYPESAFE_BASE_URL",
            "NPM_TOKEN",
            "NODE_AUTH_TOKEN",
            "PYPI_API_TOKEN",
        ]
    }
    env.update(PYTHONNOUSERSITE="1", NPM_CONFIG_USERCONFIG=os.devnull, TYPESAFE_LOG_LEVEL="off")

    def run(cmd, cwd, retry=False):
        for attempt in range(6 if retry else 1):
            try:
                subprocess.run(cmd, cwd=cwd, env=env, check=True)
                return
            except subprocess.CalledProcessError:
                if not retry or attempt == 5:
                    raise
                time.sleep(10)

    with tempfile.TemporaryDirectory(prefix="jev-consumer-") as directory:
        root = Path(directory)
        js, py = root / "js", root / "python"
        js.mkdir()
        py.mkdir()
        (js / "package.json").write_text(json.dumps({"private": True, "type": "module"}))
        npm_args = ["--offline"] if args.offline else ["--registry=https://registry.npmjs.org/"]
        run(
            [
                "npm",
                "install",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                *npm_args,
                npm_source,
                "typescript@^5.9.0",
            ],
            js,
            bool(args.registry_version),
        )
        (js / "consumer.ts").write_text(JS)
        run(
            [
                str(js / "node_modules/.bin/tsc"),
                "consumer.ts",
                "--module",
                "NodeNext",
                "--moduleResolution",
                "NodeNext",
                "--target",
                "ES2022",
                "--strict",
                "--skipLibCheck",
            ],
            js,
        )
        run(["node", "consumer.js"], js)
        (js / "policy.json").write_text(json.dumps(POLICY))
        (js / "cases.jsonl").write_text(
            json.dumps(
                {
                    "id": "clean",
                    "message": "Hello",
                    "expectedAction": "allow",
                    "labels": {"ads": False},
                }
            )
            + "\n"
        )
        run(
            [
                str(js / "node_modules/.bin/jev-moderation"),
                "validate",
                "--policy",
                "policy.json",
                "--cases",
                "cases.jsonl",
            ],
            js,
        )
        run([sys.executable, "-m", "venv", str(py / "venv")], py)
        python = str(py / "venv/bin/python")
        pip_args = ["--no-index"] if args.offline else ["--index-url", "https://pypi.org/simple"]
        if args.wheelhouse:
            pip_args += ["--find-links", str(args.wheelhouse.resolve())]
        run(
            [python, "-m", "pip", "install", "--disable-pip-version-check", *pip_args, py_source],
            py,
            bool(args.registry_version),
        )
        (py / "consumer.py").write_text(PY)
        run([python, "consumer.py"], py)
        run(
            [
                str(py / "venv/bin/jev-moderation"),
                "validate",
                "--policy",
                str(js / "policy.json"),
                "--cases",
                str(js / "cases.jsonl"),
            ],
            py,
        )
    print(f"Clean consumer installation checks passed for {version}")


if __name__ == "__main__":
    main()
