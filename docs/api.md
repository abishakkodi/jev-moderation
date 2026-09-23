# API reference

## Moderation

TypeScript:

```ts
const moderator = new Moderator({ policy, model: 'jev-1.13.0', timeoutMs: 30000 });
const result = await moderator.moderate(message, history, { signal, timeoutMs: 10000 });
```

Python:

```python
with Moderator(policy=policy, model='jev-1.13.0', timeout=30) as moderator:
    result = moderator.moderate(message, history, timeout=10)

async with AsyncModerator(policy=policy, timeout=30) as moderator:
    result = await moderator.moderate(message, history)
```

Use `apiKey` / `api_key` or `TYPESAFE_API_KEY`. Credentials are loaded lazily, so policy compilation and validation need no key. TypeScript timeouts use milliseconds; Python uses seconds. The CLI uses `--timeout-ms` in both languages.

Results have a shared JSON shape:

```json
{
  "action": "review",
  "filters": {"spam": {"probability": 0.6, "action": "review", "reason": "Configured violation definition"}},
  "matchedRuleIds": ["spam"],
  "policyVersion": "my-policy-v1",
  "policyHash": "sha256-hex",
  "model": "jev-1.13.0",
  "usage": {"input_tokens": 300, "output_tokens": 20}
}
```

Reasons are configured definitions, not model-generated explanations or identified text spans. History roles are untrusted context, not privileged model messages. The SDK does not perform enforcement actions.

## Timeouts and cancellation

TypeScript uses a total call deadline and an `AbortSignal` that reaches the upstream HTTP request and retries. Async Python uses `asyncio.wait_for` for a total deadline; cancelling its task cancels in-flight work and preserves native `asyncio.CancelledError`.

Synchronous Python uses the official synchronous client's per-request HTTP timeout and retry policy. `CancellationToken.cancel()` is cooperative: checked before and after the call; it cannot interrupt a blocking HTTP request. Use `AsyncModerator` when you need immediate in-flight cancellation or a total wall-clock deadline. A synchronous call may exceed its per-attempt timeout across retries. Python clients created by a moderator are closed by its context manager; injected clients remain caller-owned.

## Custom clients and retries

Pass `TypeSafeAdapter(officialClient)` or Python `TypeSafeAdapter(official_client)` / `AsyncTypeSafeAdapter(official_client)` as `client`. This lets callers configure official retry policies and HTTP transports. We reuse upstream retries without adding another retry loop.

You can also implement `DecisionClient.decide(request, options)` in TypeScript or `decide(request, *, timeout)` in Python. Return the raw response dictionary, or raise an exception. The async Python version must return an awaitable.

`MockClient` / `AsyncMockClient` consume a queue of raw response dictionaries or errors. Exhausting the queue is an error. `mockResponse` / `mock_response` constructs zero-usage Noul responses from `{filterId: probability}`. Mocks test application integration, not moderation quality.

## Errors

All wrapper errors derive from `ModerationError` and carry a stable `code`:

| Class | Code | Meaning |
|---|---|---|
| ConfigurationError | configuration | Invalid policy, dataset, gates, or options |
| InputError | input | Invalid message/history or request size limit |
| ResponseError | response | Missing answers, invalid probabilities, or malformed response |
| ProviderError | provider | Upstream authentication, connectivity, or exhausted retries |
| TimeoutError | timeout | Wrapper deadline, or Python upstream timeout |
| CancelledError | cancelled | TypeScript abort or cooperative Python cancellation |

Python async task cancellation remains `asyncio.CancelledError`. Errors never become moderation actions. Callers decide whether to hold messages, reject delivery, or retry later. Provider error causes are retained for debugging; evaluation reports contain sanitized error messages rather than upstream bodies.

## Evaluation

TypeScript exports `evaluate(policy, cases, options)`, `compare(left, right, cases, options)`, and `rethreshold(report, policy, cases, gates?)`. Supply an optional `moderator` for evaluations or `leftModerator` / `rightModerator` for comparisons.

Python exports synchronous `evaluate` / `compare` plus `evaluate_async` / `compare_async`. Evaluation injection uses an `AsyncModerator` in either entrypoint; synchronous entrypoints run the async evaluation engine. Use async entrypoints inside an existing event loop. Options use `left_moderator` / `right_moderator` for comparisons.

Both support `concurrency` (1–64, default 4) and `gates`. Failed cases are retained, and other cases continue. TypeScript evaluation accepts `signal`; Python evaluation can be cancelled via its asyncio task (cancels workers and propagates cancellation without a partial report).

Replay checks compiled-question and dataset fingerprints. Altered definitions, shared instructions, criteria, enabled filters, or dataset contents reject replay. Model selection is recorded in the inference fingerprint. Compare runs live again for changed questions or model selection. Replay reports zero *new* usage and latency; preserve the source report to retain original costs and timings.
