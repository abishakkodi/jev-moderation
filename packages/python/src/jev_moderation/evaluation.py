"""Labeled evaluations use the same moderator as production, with bounded concurrency."""

from __future__ import annotations

import asyncio
import math
import time
from typing import TypedDict

from typing_extensions import NotRequired

from .core import (
    Action,
    AsyncModerator,
    ConfigurationError,
    ModerationError,
    ModerationResult,
    PolicyConfig,
    QualityGates,
    TestCase,
    Usage,
    decide_result,
    digest,
    inference_hash,
    mock_response,
    resolve_policy,
    validate_cases,
    validate_schema,
)


class EvaluationError(TypedDict):
    code: str
    message: str


class CaseResult(TypedDict):
    id: str
    expectedAction: Action
    latencyMs: float
    result: NotRequired[ModerationResult]
    error: NotRequired[EvaluationError]


class FilterMetrics(TypedDict):
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    falsePositiveIds: list[str]
    falseNegativeIds: list[str]


class LatencyMetrics(TypedDict):
    mean: float
    p95: float


class EvaluationReport(TypedDict):
    schemaVersion: int
    policyHash: str
    policyVersion: str
    inferenceHash: str
    datasetHash: str
    requestedModel: str
    total: int
    completed: int
    errors: int
    accuracy: float
    reviewRate: float
    confusion: dict[Action, dict[Action, int]]
    filters: dict[str, FilterMetrics]
    latencyMs: LatencyMetrics
    usage: Usage
    failedCaseIds: list[str]
    cases: list[CaseResult]
    gateFailures: list[str]
    passed: bool
    reused: bool


class ComparisonReport(TypedDict):
    left: EvaluationReport
    right: EvaluationReport
    reused: bool
    changedCaseIds: list[str]
    passed: bool


def validate_gates(gates: QualityGates, policy: dict) -> None:
    validate_schema("gates", gates)
    ids = {f["id"] for f in policy["filters"]}
    for id in gates.get("filters", {}):
        if id not in ids:
            raise ConfigurationError(f"Unknown gate filter: {id}")


def summarize(
    policy: dict,
    model: str,
    cases: list[TestCase],
    rows: list[dict],
    gates: QualityGates | None = None,
    reused: bool = False,
) -> EvaluationReport:
    gates = {"minAccuracy": 1} if gates is None else gates
    validate_gates(gates, policy)
    actions = ["allow", "review", "block"]
    confusion = {a: {b: 0 for b in actions} for a in actions}
    filters = {
        f["id"]: dict(
            tp=0,
            fp=0,
            tn=0,
            fn=0,
            precision=None,
            recall=None,
            falsePositiveIds=[],
            falseNegativeIds=[],
        )
        for f in policy["filters"]
    }
    usage, failed = dict(input_tokens=0, output_tokens=0), []
    correct = reviews = errors = 0
    for c, row in zip(cases, rows):
        if "result" not in row:
            errors += 1
            failed.append(row["id"])
            continue
        r = row["result"]
        confusion[c["expectedAction"]][r["action"]] += 1
        if r["action"] == c["expectedAction"]:
            correct += 1
        else:
            failed.append(row["id"])
        if r["action"] == "review":
            reviews += 1
        for key in usage:
            usage[key] += r["usage"][key]
        for id, truth in c.get("labels", {}).items():
            predicted, m = r["filters"][id]["action"] != "allow", filters[id]
            if predicted and truth:
                m["tp"] += 1
            elif predicted:
                m["fp"] += 1
                m["falsePositiveIds"].append(row["id"])
            elif truth:
                m["fn"] += 1
                m["falseNegativeIds"].append(row["id"])
            else:
                m["tn"] += 1
            if predicted != truth and row["id"] not in failed:
                failed.append(row["id"])
    for m in filters.values():
        m["precision"] = m["tp"] / (m["tp"] + m["fp"]) if m["tp"] + m["fp"] else None
        m["recall"] = m["tp"] / (m["tp"] + m["fn"]) if m["tp"] + m["fn"] else None
    durations = sorted(row["latencyMs"] for row in rows)
    accuracy, review_rate, gate_failures = correct / len(cases), reviews / len(cases), []
    if "minAccuracy" in gates and accuracy < gates["minAccuracy"]:
        gate_failures.append("minAccuracy")
    if "maxReviewRate" in gates and review_rate > gates["maxReviewRate"]:
        gate_failures.append("maxReviewRate")
    for id, gate in gates.get("filters", {}).items():
        for key, metric in [("minPrecision", "precision"), ("minRecall", "recall")]:
            if key in gate and (filters[id][metric] is None or filters[id][metric] < gate[key]):
                gate_failures.append(f"{id}.{key}")
    return dict(
        schemaVersion=1,
        policyHash=digest(policy),
        policyVersion=policy["version"],
        inferenceHash=inference_hash(policy, model),
        datasetHash=digest(cases),
        requestedModel=model,
        total=len(cases),
        completed=len(cases) - errors,
        errors=errors,
        accuracy=accuracy,
        reviewRate=review_rate,
        confusion=confusion,
        filters=filters,
        latencyMs={
            "mean": sum(durations) / len(rows),
            "p95": durations[math.ceil(len(durations) * 0.95) - 1],
        },
        usage=usage,
        failedCaseIds=failed,
        cases=rows,
        gateFailures=gate_failures,
        passed=errors == 0 and not gate_failures,
        reused=reused,
    )


async def evaluate_async(
    policy: PolicyConfig,
    cases: list[TestCase],
    *,
    moderator: AsyncModerator | None = None,
    concurrency: int = 4,
    gates: QualityGates | None = None,
) -> EvaluationReport:
    resolved = resolve_policy(policy)
    validate_cases(cases, resolved)
    validate_gates({"minAccuracy": 1} if gates is None else gates, resolved)
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or not 1 <= concurrency <= 64
    ):
        raise ConfigurationError("concurrency must be an integer from 1 to 64")
    owned = moderator is None
    moderator = moderator or AsyncModerator(policy=policy)
    if digest(resolved) != digest(moderator.policy):
        raise ConfigurationError("Moderator and evaluation policies must match")
    rows = [None] * len(cases)
    next_index = 0

    async def worker():
        nonlocal next_index
        while next_index < len(cases):
            index = next_index
            next_index += 1
            c = cases[index]
            start = time.perf_counter()
            row = dict(id=c["id"], expectedAction=c["expectedAction"], latencyMs=0)
            try:
                row["result"] = await moderator.moderate(c["message"], c.get("history"))
            except Exception as e:
                row["error"] = dict(
                    code=e.code if isinstance(e, ModerationError) else "execution",
                    message=str(e) if isinstance(e, ModerationError) else "Evaluation failed",
                )
            row["latencyMs"] = (time.perf_counter() - start) * 1000
            rows[index] = row

    tasks = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(cases)))]
    try:
        await asyncio.gather(*tasks)
        return summarize(resolved, moderator.model, cases, rows, gates)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if owned:
            await moderator.close()


def evaluate(policy: PolicyConfig, cases: list[TestCase], **options) -> EvaluationReport:
    """Synchronous evaluation entrypoint. In async applications use evaluate_async."""
    return asyncio.run(evaluate_async(policy, cases, **options))


def rethreshold(
    report: dict, policy: PolicyConfig, cases: list[TestCase], gates: QualityGates | None = None
) -> EvaluationReport:
    resolved = resolve_policy(policy)
    validate_cases(cases, resolved)
    if (
        report["inferenceHash"] != inference_hash(resolved, report["requestedModel"])
        or report["datasetHash"] != digest(cases)
        or len(report["cases"]) != len(cases)
    ):
        raise ConfigurationError(
            "Replay requires identical questions, requested model, and dataset"
        )
    rows = []
    for c, row in zip(cases, report["cases"]):
        if row["id"] != c["id"]:
            raise ConfigurationError("Replay case order mismatch")
        if "result" not in row:
            rows.append({**row, "latencyMs": 0})
            continue
        r = row["result"]
        raw = mock_response({id: f["probability"] for id, f in r["filters"].items()}, r["model"])
        rows.append(
            dict(
                id=row["id"],
                expectedAction=c["expectedAction"],
                latencyMs=0,
                result=decide_result(resolved, raw),
            )
        )
    return summarize(resolved, report["requestedModel"], cases, rows, gates, True)


async def compare_async(
    left: PolicyConfig,
    right: PolicyConfig,
    cases: list[TestCase],
    *,
    left_moderator: AsyncModerator | None = None,
    right_moderator: AsyncModerator | None = None,
    concurrency: int = 4,
    gates: QualityGates | None = None,
) -> ComparisonReport:
    pa, pb = resolve_policy(left), resolve_policy(right)
    for p in (pa, pb):
        validate_cases(cases, p)
        validate_gates({"minAccuracy": 1} if gates is None else gates, p)
    a, b = (
        left_moderator or AsyncModerator(policy=left),
        right_moderator or AsyncModerator(policy=right),
    )
    if digest(a.policy) != digest(pa) or digest(b.policy) != digest(pb):
        raise ConfigurationError("Moderator and comparison policies must match")
    try:
        first = await evaluate_async(left, cases, moderator=a, concurrency=concurrency, gates=gates)
        second = (
            rethreshold(first, right, cases, gates)
            if inference_hash(pa, a.model) == inference_hash(pb, b.model)
            else await evaluate_async(
                right, cases, moderator=b, concurrency=concurrency, gates=gates
            )
        )
        changed = [
            c["id"]
            for c, x, y in zip(cases, first["cases"], second["cases"])
            if x.get("result", {}).get("action") != y.get("result", {}).get("action")
        ]
        return dict(
            left=first,
            right=second,
            reused=second["reused"],
            changedCaseIds=changed,
            passed=first["passed"] and second["passed"],
        )
    finally:
        if left_moderator is None:
            await a.close()
        if right_moderator is None:
            await b.close()


def compare(
    left: PolicyConfig, right: PolicyConfig, cases: list[TestCase], **options
) -> ComparisonReport:
    return asyncio.run(compare_async(left, right, cases, **options))
