"""The CLI never loads API keys for offline validation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .core import AsyncModerator, ModerationError, community_policy, resolve_policy, validate_cases
from .evaluation import compare_async, evaluate_async, validate_gates


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="jev-moderation")
    parser.add_argument("command", choices=["validate", "eval", "compare"])
    parser.add_argument("--policy")
    parser.add_argument("--against")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--gates")
    parser.add_argument("--output")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument("--timeout-ms", type=float, default=30000)
    args = parser.parse_args()
    try:
        policy = _read(args.policy) if args.policy else community_policy()
        cases = [
            json.loads(line)
            for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        gates = _read(args.gates) if args.gates else {"minAccuracy": 1}
        resolved = resolve_policy(policy)
        validate_cases(cases, resolved)
        validate_gates(gates, resolved)
        if args.command == "validate":
            print(f"Valid: {len(resolved['filters'])} filters, {len(cases)} cases")
            return 0

        async def run():
            async with AsyncModerator(
                policy=policy, model=args.model, timeout=args.timeout_ms / 1000
            ) as a:
                if args.command == "compare":
                    if not args.against:
                        raise ValueError("compare requires --against")
                    other = _read(args.against)
                    async with AsyncModerator(
                        policy=other, model=args.model, timeout=args.timeout_ms / 1000
                    ) as b:
                        return await compare_async(
                            policy,
                            other,
                            cases,
                            left_moderator=a,
                            right_moderator=b,
                            concurrency=args.concurrency,
                            gates=gates,
                        )
                return await evaluate_async(
                    policy, cases, moderator=a, concurrency=args.concurrency, gates=gates
                )

        report = asyncio.run(run())
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        print(text, end="")
        reports = [report["left"], report["right"]] if "left" in report else [report]
        for r in reports:
            print(
                f"{r['policyVersion']}: {r['completed']}/{r['total']} completed; accuracy={r['accuracy']:.3f} review={r['reviewRate']:.3f} errors={r['errors']}; gates={','.join(r['gateFailures']) or 'pass'}",
                file=sys.stderr,
            )
        return 2 if any(r["errors"] for r in reports) else 0 if report["passed"] else 1
    except KeyboardInterrupt:
        print("Evaluation cancelled", file=sys.stderr)
        return 130
    except (ModerationError, ValueError, OSError) as e:
        print(f"{e.code}: {e}" if isinstance(e, ModerationError) else str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
