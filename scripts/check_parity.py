"""Cross-language requests, hashes, decisions, and evaluation report conformance."""

import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "packages/python/src"))
from jev_moderation import (
    AsyncMockClient,
    AsyncModerator,
    decide_result,
    digest,
    evaluate,
    make_request,
    resolve_policy,
)

fixtures = json.loads((root / "shared/conformance.json").read_text())
expected = []
for f in fixtures:
    p = resolve_policy(f["policy"])
    expected.append(
        dict(
            request=make_request(p, f["message"], f["history"]),
            policyHash=digest(p),
            result=decide_result(p, f["raw"]),
        )
    )
js = """
import { readFileSync } from 'node:fs';
import { resolvePolicy, makeRequest, decideResult, digest, evaluate, Moderator, MockClient } from './packages/typescript/dist/index.js';
const fixtures = JSON.parse(readFileSync('shared/conformance.json', 'utf8'));
const outputs = fixtures.map(f => { const p = resolvePolicy(f.policy); return {request: makeRequest(p,f.message,f.history),policyHash:digest(p),result:decideResult(p,f.raw)}; });
const cases=fixtures.map(f=>({id:f.id,message:f.message,history:f.history,expectedAction:f.expectedAction}));
const report=await evaluate(fixtures[0].policy,cases,{moderator:new Moderator({policy:fixtures[0].policy,client:new MockClient(fixtures.map(f=>f.raw))}),concurrency:1});
delete report.latencyMs; for(const row of report.cases) delete row.latencyMs;
console.log(JSON.stringify({outputs,report}));
"""
actual = json.loads(
    subprocess.check_output(["node", "--input-type=module", "-e", js], cwd=root, text=True)
)
assert actual["outputs"] == expected, "Request/hash/result parity failure"
cases = [
    dict(id=f["id"], message=f["message"], history=f["history"], expectedAction=f["expectedAction"])
    for f in fixtures
]
report = evaluate(
    fixtures[0]["policy"],
    cases,
    moderator=AsyncModerator(
        policy=fixtures[0]["policy"], client=AsyncMockClient([f["raw"] for f in fixtures])
    ),
    concurrency=1,
)
del report["latencyMs"]
for row in report["cases"]:
    del row["latencyMs"]
assert report == actual["report"], "Evaluation report parity failure"
print(
    f"Cross-language parity passed: {len(fixtures)} requests, hashes, decisions, and full evaluation report"
)
