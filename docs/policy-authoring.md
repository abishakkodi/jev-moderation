# Authoring and evaluating policies

1. Define one narrow violation per filter. High probability must always mean a violation. Split independent conditions into independent filters.
2. Write explicit exclusions: criticism versus personal abuse, reporting versus endorsing, help-seeking versus encouragement. Use `criteria.true` and `criteria.false` when the distinction needs examples.
3. Use `extend` to preserve community defaults or `replace` for your own policy. Existing filters can override thresholds without changing their definitions. `instructions` replaces shared preset instructions when supplied.
4. Add positive, negative, ambiguous, quoted, contextual, and adversarial examples to a tuning dataset. Every case requires a unique ID, nonempty message, and expected action. Filter labels are optional, but their IDs must exist in the resolved policy.
5. Keep a separate held-out dataset. Run `eval` to measure precision, recall, review rate, and errors. Do not tune on your held-out cases.
6. Adjust review/block thresholds, then use `compare` or `rethreshold` to inspect changes. Threshold-only changes reuse results. Prompt or criteria changes need new inference. Replay preserves the actual model version recorded in the original response; it is not a fresh test of a moving model alias.
7. Version and ship the policy with its tuned model ID. Re-evaluate on new model versions, new languages, or changes in user content.

Filter thresholds must satisfy `0 <= review < block <= 1`. Exact boundaries are inclusive. A probability in `[review, block)` yields review, and `>= block` yields block. The overall action is the highest per-filter action; `matchedRuleIds` includes both review and block filters.

A Noul asks whether a rule was violated; its probability is not a measure of severity. Use narrow definitions such as credible threats rather than treating uncertainty as mild harm. Rule IDs are correlation keys, so the full meaning must appear in the definition.

The immutable resolved policy snapshot is hashed using a shared canonical tree representation. Numbers are represented by IEEE-754 bytes to avoid Python/JavaScript numeric formatting differences. Changing effective definitions, instructions, thresholds, criteria, or policy version changes the hash. Disabling a rule removes it from the resolved policy. Compiler instructions are included in the separate inference hash used for replay checks.

## Quality gates

```json
{
  "minAccuracy": 0.95,
  "maxReviewRate": 0.15,
  "filters": {
    "harassment": { "minPrecision": 0.95, "minRecall": 0.9 }
  }
}
```

A gates file replaces the default `minAccuracy: 1`. Add that field if you still want an overall accuracy gate. Precision/recall count **any flag (review or block)** as positive; use the action confusion matrix to distinguish automated blocks from review referrals. A metric with no denominator is `null`, and a gate on it fails rather than claiming perfect accuracy.

The action confusion matrix includes completed cases only. Errors appear separately, fail the run, and count against total-case accuracy. Review rate uses the total number of cases as its denominator. False-positive/negative IDs cover labeled, completed filter judgments. Latency includes retries and errors. Usage totals cover successful responses; failed calls may still incur provider charges and are not assigned invented token counts.

Reports omit message/history text but include caller-provided case IDs and configured rule descriptions. Treat reports and IDs according to your own privacy requirements. Replayed reports are trusted local artifacts, not cryptographically signed evidence of inference.
