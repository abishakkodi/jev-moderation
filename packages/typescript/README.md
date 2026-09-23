# Jev Moderation for TypeScript

Server-side moderation with a community preset, custom filters, context, and evaluation tools. Requires Node.js 20.19+ and your own TypeSafe API key.

```ts
import { Moderator } from 'jev-moderation';

// Reads TYPESAFE_API_KEY. Each call goes directly to TypeSafe.
const moderator = new Moderator();
const result = await moderator.moderate('Thanks for helping!');
console.log(result.action); // allow | review | block
```

To customize, pass a JSON-compatible policy:

```ts
const moderator = new Moderator({
  policy: {
    schemaVersion: 1, version: 'custom-v1', mode: 'extend',
    filters: [
      { id: 'spam', review: 0.6, block: 0.95 },
      { id: 'ads', definition: 'Unsolicited advertising unrelated to the discussion.' },
    ],
  },
});
```

`extend` overrides preset filters by ID; `replace` starts with your own rules. `enabled: false` explicitly removes an existing rule. Rules support `definition`, optional `criteria: {true, false}`, and `review`/`block` thresholds. An optional shared `instructions` string goes into every question.

`moderate(message, history?, {signal?, timeoutMs?})` returns per-filter probabilities, actions and configured descriptions, matched IDs, policy version/hash, resolved model version, and usage. History contains `{role, content}` entries and is only context for the target message. Use `AbortSignal` for cancellation. Default total deadline: 30 seconds.

## Evaluation

```sh
jev-moderation validate --cases cases.jsonl --policy policy.json
jev-moderation eval --cases cases.jsonl --policy policy.json --output report.json
jev-moderation compare --cases cases.jsonl --policy policy.json --against candidate.json
```

Each JSONL record has `id`, `message`, `expectedAction`, optional `history`, and optional `labels` mapping filter IDs to booleans. Live evaluation is billable. `validate` is offline.

`evaluate`, `compare`, and `rethreshold` are also exported for test runners. `MockClient` plus `mockResponse({ruleId: probability})` provides offline integration tests. A mock checks integration, not model accuracy.

The default CI gate requires action accuracy 1.0; `--gates gates.json` replaces it. Available gates: `minAccuracy`, `maxReviewRate`, and `filters: {id: {minPrecision, minRecall}}`. Both review and block count as positive filter flags. Reports retain error cases, confusion matrices, precision/recall, latency, and usage. CLI exit codes are 0 success, 1 quality failure, 2 invalid input or execution error.

Defaults: `community-v1`, model `jev-1.13.0`, review ≥ 0.5, block ≥ 0.9. Thresholds are uncalibrated starting points. Requests above 24,000 UTF-8 bytes including rules are rejected without truncation. Typed errors never become allow decisions. No message logging by this SDK; injected clients retain their logging settings.

Uses the [official TypeSafe API](https://docs.typesafe.ai/api). This package is not an official TypeSafe SDK.
