"""Shared policy semantics and official TypeSafe client adapters."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import struct
import threading
from importlib.resources import files
from typing import Any, Literal, Protocol, TypedDict

from jsonschema import Draft7Validator
from typesafe_sdk import (
    AsyncTypeSafeClient,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeClient,
)
from typing_extensions import NotRequired

Action = Literal["allow", "review", "block"]


class FilterConfig(TypedDict):
    id: str
    definition: NotRequired[str]
    criteria: NotRequired[dict[str, str]]
    review: NotRequired[float]
    block: NotRequired[float]
    enabled: NotRequired[bool]


class PolicyConfig(TypedDict):
    schemaVersion: Literal[1]
    version: str
    mode: Literal["extend", "replace"]
    preset: NotRequired[Literal["community-v1"]]
    instructions: NotRequired[str]
    filters: list[FilterConfig]


class HistoryMessage(TypedDict):
    role: Literal["user", "assistant", "system"]
    content: str


class TestCase(TypedDict):
    id: str
    message: str
    history: NotRequired[list[HistoryMessage]]
    expectedAction: Action
    labels: NotRequired[dict[str, bool]]


class FilterResult(TypedDict):
    probability: float
    action: Action
    reason: str


class Usage(TypedDict):
    input_tokens: int
    output_tokens: int


class ModerationResult(TypedDict):
    action: Action
    filters: dict[str, FilterResult]
    matchedRuleIds: list[str]
    policyVersion: str
    policyHash: str
    model: str
    usage: Usage


class FilterGate(TypedDict, total=False):
    minPrecision: float
    minRecall: float


class QualityGates(TypedDict, total=False):
    minAccuracy: float
    maxReviewRate: float
    filters: dict[str, FilterGate]


class DecisionClient(Protocol):
    def decide(self, request: dict, *, timeout: float) -> Any: ...


class AsyncDecisionClient(Protocol):
    async def decide(self, request: dict, *, timeout: float) -> Any: ...


class ModerationError(Exception):
    code = "moderation"


class ConfigurationError(ModerationError):
    code = "configuration"


class InputError(ModerationError):
    code = "input"


class ResponseError(ModerationError):
    code = "response"


class ProviderError(ModerationError):
    code = "provider"


class CancelledError(ModerationError):
    code = "cancelled"


class TimeoutError(ModerationError):
    code = "timeout"


def _asset(name: str) -> dict:
    return json.loads(files("jev_moderation").joinpath("data", name).read_text(encoding="utf-8"))


COMPILER = _asset("compiler.json")
_VALIDATORS = {
    key: Draft7Validator(_asset(name))
    for key, name in [
        ("policy", "policy.schema.json"),
        ("case", "case.schema.json"),
        ("gates", "gates.schema.json"),
    ]
}


def validate_schema(kind: str, value: Any) -> None:
    errors = list(_VALIDATORS[kind].iter_errors(value))
    if errors:
        # Do not include the rejected value (which may contain private message text).
        path = ".".join(str(x) for x in errors[0].absolute_path)
        raise ConfigurationError(f"Invalid {kind} at {path or 'root'}: {errors[0].validator}")

    def finite(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ConfigurationError("Non-finite numbers are not JSON values")
        if isinstance(v, dict):
            for item in v.values():
                finite(item)
        elif isinstance(v, list):
            for item in v:
                finite(item)

    finite(value)


def community_policy() -> PolicyConfig:
    return _asset("community-v1.json")


def resolve_policy(config: PolicyConfig | None = None) -> dict:
    config = community_policy() if config is None else config
    validate_schema("policy", config)
    if config["mode"] == "replace" and "preset" in config:
        raise ConfigurationError("preset is only valid in extend mode")
    base = community_policy() if config["mode"] == "extend" else {}
    filters = {f["id"]: copy.deepcopy(f) for f in base.get("filters", [])}
    seen = set()
    for override in config["filters"]:
        id = override["id"]
        if id in seen:
            raise ConfigurationError(f"Duplicate filter: {id}")
        seen.add(id)
        if override.get("enabled") is False:
            if id not in filters:
                raise ConfigurationError(f"Cannot disable unknown filter: {id}")
            del filters[id]
        else:
            filters[id] = {**filters.get(id, {}), **override}
    resolved = []
    for id, f in sorted(filters.items()):
        if not f.get("definition", "").strip():
            raise ConfigurationError(f"Missing definition: {id}")
        review, block = f.get("review", 0.5), f.get("block", 0.9)
        if review >= block:
            raise ConfigurationError(f"review must be less than block: {id}")
        rule = dict(id=id, definition=f["definition"], review=review, block=block)
        if "criteria" in f:
            rule["criteria"] = f["criteria"]
        resolved.append(rule)
    if not resolved:
        raise ConfigurationError("At least one enabled filter is required")
    return copy.deepcopy(
        dict(
            schemaVersion=1,
            version=config["version"],
            instructions=config.get("instructions", base.get("instructions", "")),
            filters=resolved,
        )
    )


def digest(value: Any) -> str:
    def tree(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return ["number", struct.pack(">d", 0.0 if v == 0 else float(v)).hex()]
        if isinstance(v, list):
            return ["array", [tree(x) for x in v]]
        if isinstance(v, dict):
            return ["object", [[k, tree(v[k])] for k in sorted(v)]]
        return v

    return hashlib.sha256(
        json.dumps(tree(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def compile_questions(policy: dict) -> dict:
    return {
        f["id"]: {
            "type": "noul",
            "instructions": {
                "task": COMPILER["instructions"],
                "policy": policy["instructions"],
                "rule": f["definition"],
            },
            **({"criteria": f["criteria"]} if "criteria" in f else {}),
        }
        for f in policy["filters"]
    }


def inference_hash(policy: dict, model: str) -> str:
    return digest({"model": model, "questions": compile_questions(policy)})


def validate_cases(cases: list[TestCase], policy: dict) -> None:
    if not isinstance(cases, list) or not cases:
        raise ConfigurationError("Dataset must contain at least one case")
    ids, rules = set(), {f["id"] for f in policy["filters"]}
    for c in cases:
        validate_schema("case", c)
        if c["id"] in ids:
            raise ConfigurationError(f"Duplicate case ID: {c['id']}")
        ids.add(c["id"])
        for id in c.get("labels", {}):
            if id not in rules:
                raise ConfigurationError(f"Unknown label: {id}")


def make_request(
    policy: dict,
    message: str,
    history: list[HistoryMessage] | None = None,
    model: str = COMPILER["defaultModel"],
) -> dict:
    history = [] if history is None else history
    try:
        validate_schema(
            "case", dict(id="input", message=message, history=history, expectedAction="allow")
        )
    except ConfigurationError as e:
        raise InputError(
            "message must be nonempty text and history must contain role/content messages"
        ) from e
    request = dict(
        model=model,
        state={"target": message, "history": copy.deepcopy(history)},
        questions=compile_questions(policy),
    )
    if (
        len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > COMPILER["maxRequestBytes"]
    ):
        raise InputError(
            f"Request exceeds {COMPILER['maxRequestBytes']} UTF-8 bytes; shorten content or policy explicitly"
        )
    return request


def decide_result(policy: dict, raw: Any) -> ModerationResult:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("model"), str)
        or not raw["model"].strip()
        or not isinstance(raw.get("answers"), dict)
        or not isinstance(raw.get("usage"), dict)
    ):
        raise ResponseError("Invalid model response")
    for key in ("input_tokens", "output_tokens"):
        n = raw["usage"].get(key)
        if (
            isinstance(n, bool)
            or not isinstance(n, (int, float))
            or not math.isfinite(n)
            or n != int(n)
            or n < 0
            or n > 2**53 - 1
        ):
            raise ResponseError("Invalid token usage")
    filters, action = {}, "allow"
    for f in policy["filters"]:
        a = raw["answers"].get(f["id"])
        if not isinstance(a, dict) or a.get("type") != "noul":
            raise ResponseError(f"Missing or invalid Noul answer: {f['id']}")
        p = a.get("noul")
        if (
            isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or not 0 <= p <= 1
        ):
            raise ResponseError(f"Missing or invalid Noul answer: {f['id']}")
        decision = "block" if p >= f["block"] else "review" if p >= f["review"] else "allow"
        filters[f["id"]] = dict(probability=p, action=decision, reason=f["definition"])
        if decision == "block" or (decision == "review" and action == "allow"):
            action = decision
    return dict(
        action=action,
        filters=filters,
        matchedRuleIds=[id for id, f in filters.items() if f["action"] != "allow"],
        policyVersion=policy["version"],
        policyHash=digest(policy),
        model=raw["model"],
        usage=copy.deepcopy(raw["usage"]),
    )


class TypeSafeAdapter:
    def __init__(self, client: TypeSafeClient):
        self.client = client

    def decide(self, request: dict, *, timeout: float) -> Any:
        result = self.client.system_one(**request, timeout=timeout)
        try:
            return result.raw_http_response.json()
        except (ValueError, AttributeError) as e:
            raise ResponseError("Model response is not JSON") from e


class AsyncTypeSafeAdapter:
    def __init__(self, client: AsyncTypeSafeClient):
        self.client = client

    async def decide(self, request: dict, *, timeout: float) -> Any:
        result = await self.client.system_one(**request, timeout=timeout)
        try:
            return result.raw_http_response.json()
        except (ValueError, AttributeError) as e:
            raise ResponseError("Model response is not JSON") from e


class CancellationToken:
    """Cooperative synchronous cancellation; use AsyncModerator for in-flight cancellation."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError("Moderation cancelled")


def _timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigurationError("timeout must be positive seconds")
    return value


def _translate(error: Exception) -> ModerationError:
    if isinstance(error, ModerationError):
        return error
    if isinstance(error, TypeSafeAPIResponseValidationError):
        return ResponseError("Invalid model response")
    if isinstance(error, TypeSafeAPITimeoutError):
        return TimeoutError("Moderation request timed out")
    return ProviderError("TypeSafe request failed after configured retries")


class _BaseModerator:
    def __init__(
        self,
        *,
        policy: PolicyConfig | None = None,
        model: str = COMPILER["defaultModel"],
        api_key: str | None = None,
        client: Any = None,
        timeout: float = 30,
    ):
        self._policy = resolve_policy(policy)
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("model must be nonempty")
        self.model, self.timeout, self._api_key, self._client = (
            model,
            _timeout(timeout),
            api_key,
            client,
        )
        self._owned = None

    @property
    def policy(self) -> dict:
        return copy.deepcopy(self._policy)


class Moderator(_BaseModerator):
    def moderate(
        self,
        message: str,
        history: list[HistoryMessage] | None = None,
        *,
        timeout: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModerationResult:
        request = make_request(self._policy, message, history, self.model)
        seconds = _timeout(self.timeout if timeout is None else timeout)
        if cancellation:
            cancellation.check()
        try:
            if self._client is None:
                self._owned = TypeSafeClient(api_key=self._api_key)
                self._client = TypeSafeAdapter(self._owned)
            raw = self._client.decide(request, timeout=seconds)
            if cancellation:
                cancellation.check()
            return decide_result(self._policy, raw)
        except Exception as e:
            translated = _translate(e)
            if translated is e:
                raise
            raise translated from e

    def close(self) -> None:
        if self._owned is not None:
            self._owned.close()
            self._owned = None
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncModerator(_BaseModerator):
    async def moderate(
        self,
        message: str,
        history: list[HistoryMessage] | None = None,
        *,
        timeout: float | None = None,
    ) -> ModerationResult:
        request = make_request(self._policy, message, history, self.model)
        seconds = _timeout(self.timeout if timeout is None else timeout)
        try:
            if self._client is None:
                self._owned = AsyncTypeSafeClient(api_key=self._api_key)
                self._client = AsyncTypeSafeAdapter(self._owned)
            raw = await asyncio.wait_for(self._client.decide(request, timeout=seconds), seconds)
            return decide_result(self._policy, raw)
        except asyncio.TimeoutError as e:
            raise TimeoutError("Moderation deadline exceeded") from e
        # asyncio.CancelledError intentionally propagates so task cancellation stays native.
        except Exception as e:
            translated = _translate(e)
            if translated is e:
                raise
            raise translated from e

    async def close(self) -> None:
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


class MockClient:
    def __init__(self, responses: list[Any]):
        self.responses, self.requests = list(responses), []

    def decide(self, request: dict, *, timeout: float = 30) -> Any:
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise RuntimeError("Mock response queue exhausted")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class AsyncMockClient(MockClient):
    async def decide(self, request: dict, *, timeout: float = 30) -> Any:
        return super().decide(request, timeout=timeout)


def mock_response(probabilities: dict[str, float], model: str = COMPILER["defaultModel"]) -> dict:
    return dict(
        model=model,
        answers={id: dict(type="noul", noul=p) for id, p in probabilities.items()},
        usage=dict(input_tokens=0, output_tokens=0),
    )
