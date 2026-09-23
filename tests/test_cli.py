"""Exercise both packaged CLI entrypoints without credentials or network access."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = """
let calls=0;
globalThis.fetch=async (_url,init)=>{
  calls++;
  if(process.env.TEST_FAILURE==='1') return new Response('{}',{status:401});
  const body=JSON.parse(init.body);
  return Response.json({model:'jev-1.13.0',answers:Object.fromEntries(Object.keys(body.questions).map(id=>[id,{type:'noul',noul:body.state.target==='bad'?0.95:0.1}])),usage:{input_tokens:10,output_tokens:1}});
};
process.on('exit',()=>console.error('CALLS='+calls));
process.argv=['node','cli',...JSON.parse(process.env.TEST_ARGS)];
await import('./packages/typescript/dist/cli.js');
"""
PY = """
import json, os, sys
from unittest.mock import patch
import httpx2
from typesafe_sdk import AsyncTypeSafeClient
from jev_moderation.cli import main
calls = 0
def handler(request):
    global calls
    calls += 1
    if os.environ.get('TEST_FAILURE') == '1': return httpx2.Response(401, json={})
    body = json.loads(request.content)
    return httpx2.Response(200, json={'model':'jev-1.13.0','answers':{id:{'type':'noul','noul':.95 if body['state']['target']=='bad' else .1} for id in body['questions']},'usage':{'input_tokens':10,'output_tokens':1}})
def client(**kwargs):
    return AsyncTypeSafeClient(**kwargs, transport=httpx2.MockTransport(handler))
sys.argv=['jev-moderation',*json.loads(os.environ['TEST_ARGS'])]
with patch('jev_moderation.core.AsyncTypeSafeClient', client): code=main()
print('CALLS='+str(calls), file=sys.stderr)
sys.exit(code)
"""


@pytest.fixture(params=["typescript", "python"])
def cli(request, tmp_path):
    policy = {
        "schemaVersion": 1,
        "version": "v1",
        "mode": "replace",
        "filters": [{"id": "abuse", "definition": "Targeted abuse."}],
    }
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    cases = [
        {"id": "bad", "message": "bad", "expectedAction": "block", "labels": {"abuse": True}},
        {
            "id": "good",
            "message": "private benign message",
            "expectedAction": "allow",
            "labels": {"abuse": False},
        },
    ]
    (tmp_path / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in cases))

    def run(command, *args, failure=False):
        env = {
            **os.environ,
            "TEST_ARGS": json.dumps(
                [
                    command,
                    "--policy",
                    str(tmp_path / "policy.json"),
                    "--cases",
                    str(tmp_path / "cases.jsonl"),
                    *args,
                ]
            ),
            "TYPESAFE_API_KEY": "test-key",
            "TYPESAFE_LOG_LEVEL": "off",
            "TEST_FAILURE": "1" if failure else "0",
        }
        cmd = (
            ["node", "--input-type=module", "-e", JS]
            if request.param == "typescript"
            else [sys.executable, "-c", PY]
        )
        return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)

    return run, tmp_path, policy


def test_offline_validation(cli):
    run, _, _ = cli
    r = run("validate")
    assert r.returncode == 0, r.stderr
    assert "CALLS=0" in r.stderr


def test_eval_output_and_privacy(cli):
    run, path, _ = cli
    r = run("eval", "--output", str(path / "report.json"))
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report == json.loads((path / "report.json").read_text())
    assert report["accuracy"] == 1 and report["usage"]["input_tokens"] == 20
    assert "private benign message" not in r.stdout
    assert "CALLS=2" in r.stderr


def test_quality_failure_and_comparison_reuse(cli):
    run, path, policy = cli
    policy["version"] = "v2"
    policy["filters"][0]["block"] = 0.99
    (path / "right.json").write_text(json.dumps(policy))
    r = run("compare", "--against", str(path / "right.json"))
    assert r.returncode == 1, r.stderr
    report = json.loads(r.stdout)
    assert report["reused"] and report["right"]["accuracy"] == 0.5
    assert report["right"]["usage"]["input_tokens"] == 0
    assert "CALLS=2" in r.stderr


def test_api_errors_fail_and_remain_in_report(cli):
    run, _, _ = cli
    r = run("eval", failure=True)
    assert r.returncode == 2, r.stderr
    report = json.loads(r.stdout)
    assert report["errors"] == report["total"] == 2
    assert report["completed"] == 0 and not report["passed"]
    assert "CALLS=2" in r.stderr


def test_invalid_config_prevents_requests(cli):
    run, path, policy = cli
    policy["filters"] = []
    (path / "policy.json").write_text(json.dumps(policy))
    r = run("eval")
    assert r.returncode == 2
    assert "CALLS=0" in r.stderr
