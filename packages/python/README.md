# Jev Moderation for Python

Text moderation with community defaults, custom filters, optional history, and test tools. Requires Python 3.10+ and your own `TYPESAFE_API_KEY`. Calls go directly to TypeSafe.

```python
from jev_moderation import Moderator, AsyncModerator

with Moderator() as moderator:
    result = moderator.moderate("Thanks for helping!")
    print(result["action"])  # allow | review | block

# In an async function:
# async with AsyncModerator() as moderator:
#     result = await moderator.moderate("Thanks!")
```

Pass `policy={"schemaVersion": 1, "version": "custom-v1", "mode": "extend", "filters": [{"id": "spam", "review": 0.6, "block": 0.95}]}` to either moderator. `replace` starts with an empty filter set; new filters require `id` and `definition`. `enabled: False` disables an existing rule. Optional `criteria` has `true` and `false` descriptions. Shared `instructions` go into every question. Policy JSON is portable to the TypeScript SDK.

`moderate(message, history=None, timeout=None)` returns per-filter probabilities, actions and configured descriptions, matched rule IDs, policy version/hash, model, and usage. History is a list of `{role, content}` dictionaries used only to interpret the target. Errors never become allow decisions.

Timeouts use seconds. Async calls have a total deadline and support native asyncio task cancellation. Sync calls use upstream per-request timeouts/retries; `CancellationToken` is cooperative before/after the call and cannot interrupt blocking HTTP. Context managers close owned connections.

## Test tools

```sh
jev-moderation validate --cases cases.jsonl --policy policy.json
jev-moderation eval --cases cases.jsonl --policy policy.json --output report.json
jev-moderation compare --cases cases.jsonl --policy policy.json --against candidate.json
```

Each JSONL record has `id`, `message`, `expectedAction`, optional `history`, and optional `labels` mapping filter IDs to booleans. Validation is offline; eval/compare perform billable inference. CLI timeouts use `--timeout-ms`.

Use `evaluate`/`compare` from synchronous code, or `evaluate_async`/`compare_async` inside an event loop. Inject an `AsyncModerator` to customize evaluation transport. `MockClient`/`AsyncMockClient` and `mock_response` support offline integration tests. `rethreshold` reuses recorded probabilities only for identical questions/model selection and dataset.

Default gate: exact action accuracy 1.0. `--gates` replaces it with `minAccuracy`, `maxReviewRate`, and/or per-filter `minPrecision`/`minRecall`. Review and block both count as positive filter flags. CLI exit codes: 0 success, 1 quality failure, 2 invalid input/execution error, 130 interruption. Error cases remain in reports and fail the run.

Default policy: `community-v1`; model: `jev-1.13.0`; review ≥ 0.5 and block ≥ 0.9. These are uncalibrated thresholds. Requests above 24,000 UTF-8 bytes including compiled rules are rejected without truncation. No message logging by this package. The upstream SDK can log bodies if you enable `TYPESAFE_LOG_LEVEL=debug`.

Uses the [official TypeSafe API](https://docs.typesafe.ai/api). This package is not an official TypeSafe SDK.
