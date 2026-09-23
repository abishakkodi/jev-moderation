# Jev Moderation

TypeScript and Python SDKs for moderating text with TypeSafe's Jev model. Use the bundled community policy, extend it, or replace it with your own filters. Both SDKs share policy schemas, prompts, and conformance fixtures.

The npm and PyPI packages are not yet published. Calls go directly from your server to TypeSafe using **your TypeSafe API key**. There is no hosted moderation service.

## Quick start

Build and install locally:

```sh
npm ci
npm run build
python3 -m venv .venv
.venv/bin/pip install -e 'packages/python[dev]'
export TYPESAFE_API_KEY='your-key'
```

TypeScript (use the workspace package, or install the tarball produced by `npm pack -w jev-moderation` in another app):

```ts
import { Moderator } from 'jev-moderation';

const moderator = new Moderator();
const result = await moderator.moderate('Thanks for helping!');
console.log(result.action); // allow | review | block
console.log(result.filters); // probabilities, decisions, configured rule descriptions
```

Python:

```python
from jev_moderation import Moderator

with Moderator() as moderator:
    result = moderator.moderate("Thanks for helping!")
    print(result["action"])
```

For async Python, use `async with AsyncModerator()` and `await moderator.moderate(...)`.

## Custom filters

A policy is JSON-compatible and portable between the two SDKs:

```json
{
  "schemaVersion": 1,
  "version": "my-community-v1",
  "mode": "extend",
  "preset": "community-v1",
  "filters": [
    { "id": "spam", "review": 0.6, "block": 0.95 },
    {
      "id": "off_topic_ads",
      "definition": "Unsolicited advertising unrelated to the community topic. Relevant recommendations are allowed.",
      "criteria": {
        "true": "An unsolicited, unrelated promotional message.",
        "false": "Relevant discussion or a recommendation requested by someone."
      },
      "review": 0.5,
      "block": 0.9
    }
  ]
}
```

Pass it as `new Moderator({ policy })` or `Moderator(policy=policy)`. `mode: "replace"` starts from an empty policy; each new rule must have a definition. Extension overrides existing filters by ID. Disable an existing preset rule explicitly with `{ "id": "spam", "enabled": false }`. No configuration may resolve to zero filters.

An optional `instructions` string is included in **every** Jev question. In extension mode, supplying it replaces the preset's shared instructions. There is no separate Jev system prompt; each rule becomes a Noul question with shared policy instructions and optional yes/no criteria.

## Test your policy

Each JSONL line is a labeled case:

```json
{"id":"friendly","message":"Thanks!","expectedAction":"allow","labels":{"harassment":false}}
```

Optional `history` is an ordered list of `{ "role": "user|assistant|system", "content": "..." }`. History supplies context; only the target message is moderated. Optional filter labels measure detection; **review and block both count as positive flags**.

Both packages expose the same CLI arguments:

```sh
# TypeScript CLI; Python equivalent starts with .venv/bin/jev-moderation
node packages/typescript/dist/cli.js validate --cases shared/examples.jsonl
node packages/typescript/dist/cli.js eval --cases shared/examples.jsonl --output report.json
node packages/typescript/dist/cli.js compare --cases shared/examples.jsonl \
  --against examples/custom-policy.json --gates examples/quality-gates.json
```

`validate` is offline. `eval` and `compare` make billable API calls. The bundled examples are starter cases, **not a measured accuracy benchmark**. Live evaluations are never part of default tests.

- Default gate: exact action accuracy of 1.0. `--gates file.json` replaces it with your chosen gates; `{}` explicitly requests reporting without quality gates.
- Exit codes: `0` success, `1` quality gate failure, `2` invalid input or execution/API error. Interrupted Python CLI returns `130`.
- JSON report goes to stdout and optionally `--output`; a short summary goes to stderr.
- Reports include confusion matrices, per-filter precision/recall, false-positive/negative IDs, review rate, latency, usage, error rows, and failed case IDs. Message bodies are omitted.
- API errors always fail the run and stay in the total-case denominator for accuracy. Undefined precision/recall is `null` and fails a gate that requires that metric.
- `compare` reuses probabilities only when compiled questions, model selection, and dataset are unchanged. Different rule definitions trigger new inference. Reused results show zero new token usage and zero inference latency.

See [policy authoring](docs/policy-authoring.md), [API and errors](docs/api.md), and runnable [Vitest](examples/custom-filter.test.ts) / [pytest](examples/test_custom_filter.py) examples.

## Verify and build

```sh
npm test
.venv/bin/pytest packages/python/tests tests examples/test_custom_filter.py
npm run test:parity
python3 scripts/sync_shared.py --check
.venv/bin/ruff check packages/python scripts

mkdir -p dist
npm pack -w jev-moderation --pack-destination dist
.venv/bin/python -m build packages/python --outdir dist
```

The canonical assets live in `shared/`. `npm run build` copies them into each package before building. Run `python3 scripts/sync_shared.py` before a Python-only build. CI checks asset drift and both language suites; parity tests compare full requests, hashes, decisions, and reports.

## Defaults and limits

The preset covers harassment, hate, threats, explicit sexual content, self-harm encouragement, scams, and spam. Ordinary disagreement, profanity alone, help-seeking, and non-endorsing quotation are permitted. The preset is versioned as `community-v1`.

Thresholds start at `review >= 0.5`, `block >= 0.9`; block wins over review, which wins over allow. These are uncalibrated starting values, not accuracy guarantees. Jev probabilities are violation probabilities, not severity scores. Review is a recommendation returned to your app; this SDK does not operate a review queue.

The model defaults to `jev-1.13.0`. Requests have a conservative **24,000 UTF-8 byte** limit including compiled questions, rather than pretending to count Jev tokens. Oversized input raises an error; nothing is silently truncated. Text only; the preset is intended for English evaluation first.

The SDK does not log message bodies. The upstream Python SDK can log bodies if you explicitly enable `TYPESAFE_LOG_LEVEL=debug`; avoid that for private content. Custom TypeSafe clients retain their own logging settings. Data is sent to TypeSafe for inference; no local inference or provider data-retention guarantee is implied.

Official references: [API](https://docs.typesafe.ai/api), [Noul questions](https://docs.typesafe.ai/primitives/noul), [models](https://docs.typesafe.ai/models), [JavaScript client](https://docs.typesafe.ai/sdk/javascript), [Python client](https://docs.typesafe.ai/sdk/python). Integration tested against JS `0.6.0` and Python `0.7.1` using mocked HTTP transports. Live classification accuracy has not been measured.
