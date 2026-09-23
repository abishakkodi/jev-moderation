"""Explicit, billable evaluation. Separate tuning and held-out reports; never changes policy."""

import argparse
import json
import os
from pathlib import Path

from jev_moderation import community_policy, evaluate

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["tuning", "held-out"], default="tuning")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if not os.environ.get("TYPESAFE_API_KEY"):
        parser.error("Set TYPESAFE_API_KEY in your environment or GitHub Actions secrets")
    policy = json.loads(args.policy.read_text()) if args.policy else community_policy()
    cases = [
        json.loads(line)
        for line in (ROOT / "evaluation" / f"{args.split}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    gates = json.loads((ROOT / "evaluation/gates.json").read_text())
    report = evaluate(policy, cases, concurrency=args.concurrency, gates=gates)
    path = args.output or ROOT / "reports" / f"{args.split}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{args.split}: {report['completed']}/{report['total']} completed; accuracy={report['accuracy']:.3f}; errors={report['errors']}; passed={report['passed']}"
    )
    print(f"Report: {path}")
    return 2 if report["errors"] else 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
