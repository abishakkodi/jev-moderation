import {
  Moderator,
  ConfigurationError,
  ModerationError,
  decideResult,
  digest,
  inferenceHash,
  mockResponse,
  resolvePolicy,
  validateCases,
  validateSchema,
  type Policy,
  type PolicyConfig,
  type TestCase,
  type QualityGates,
  type ModerationResult,
  type Action,
} from "./core.js";
export interface CaseResult {
  id: string;
  expectedAction: Action;
  latencyMs: number;
  result?: ModerationResult;
  error?: { code: string; message: string };
}
export interface FilterMetrics {
  tp: number;
  fp: number;
  tn: number;
  fn: number;
  precision: number | null;
  recall: number | null;
  falsePositiveIds: string[];
  falseNegativeIds: string[];
}
export interface EvaluationReport {
  schemaVersion: 1;
  policyHash: string;
  policyVersion: string;
  inferenceHash: string;
  datasetHash: string;
  requestedModel: string;
  total: number;
  completed: number;
  errors: number;
  accuracy: number;
  reviewRate: number;
  confusion: Record<Action, Record<Action, number>>;
  filters: Record<string, FilterMetrics>;
  latencyMs: { mean: number; p95: number };
  usage: { input_tokens: number; output_tokens: number };
  failedCaseIds: string[];
  cases: CaseResult[];
  gateFailures: string[];
  passed: boolean;
  reused: boolean;
}
export function validateGates(gates: QualityGates, policy: Policy): void {
  validateSchema("gates", gates);
  for (const id of Object.keys(gates.filters ?? {}))
    if (!policy.filters.some((f) => f.id === id))
      throw new ConfigurationError(`Unknown gate filter: ${id}`);
}
export function summarize(
  policy: Policy,
  model: string,
  cases: TestCase[],
  rows: CaseResult[],
  gates: QualityGates = { minAccuracy: 1 },
  reused = false,
): EvaluationReport {
  validateGates(gates, policy);
  const actions: Action[] = ["allow", "review", "block"];
  const confusion = Object.fromEntries(
    actions.map((a) => [a, Object.fromEntries(actions.map((b) => [b, 0]))]),
  ) as EvaluationReport["confusion"];
  const filters = Object.fromEntries(
    policy.filters.map((f) => [
      f.id,
      {
        tp: 0,
        fp: 0,
        tn: 0,
        fn: 0,
        precision: null,
        recall: null,
        falsePositiveIds: [],
        falseNegativeIds: [],
      },
    ]),
  ) as Record<string, FilterMetrics>;
  const usage = { input_tokens: 0, output_tokens: 0 },
    failedCaseIds: string[] = [];
  let correct = 0,
    reviews = 0,
    errors = 0;
  rows.forEach((row, i) => {
    if (!row.result) {
      errors++;
      failedCaseIds.push(row.id);
      return;
    }
    const r = row.result;
    confusion[cases[i].expectedAction][r.action]++;
    if (r.action === cases[i].expectedAction) correct++;
    else failedCaseIds.push(row.id);
    if (r.action === "review") reviews++;
    usage.input_tokens += r.usage.input_tokens;
    usage.output_tokens += r.usage.output_tokens;
    for (const [id, truth] of Object.entries(cases[i].labels ?? {})) {
      const predicted = r.filters[id].action !== "allow",
        m = filters[id];
      if (predicted && truth) m.tp++;
      else if (predicted) {
        m.fp++;
        m.falsePositiveIds.push(row.id);
      } else if (truth) {
        m.fn++;
        m.falseNegativeIds.push(row.id);
      } else m.tn++;
      if (predicted !== truth && !failedCaseIds.includes(row.id))
        failedCaseIds.push(row.id);
    }
  });
  for (const m of Object.values(filters)) {
    m.precision = m.tp + m.fp ? m.tp / (m.tp + m.fp) : null;
    m.recall = m.tp + m.fn ? m.tp / (m.tp + m.fn) : null;
  }
  const durations = rows.map((r) => r.latencyMs).sort((a, b) => a - b);
  const accuracy = correct / cases.length,
    reviewRate = reviews / cases.length,
    gateFailures: string[] = [];
  if (gates.minAccuracy !== undefined && accuracy < gates.minAccuracy)
    gateFailures.push("minAccuracy");
  if (gates.maxReviewRate !== undefined && reviewRate > gates.maxReviewRate)
    gateFailures.push("maxReviewRate");
  for (const [id, gate] of Object.entries(gates.filters ?? {})) {
    for (const [key, metric] of [
      ["minPrecision", "precision"],
      ["minRecall", "recall"],
    ] as const) {
      if (
        gate[key] !== undefined &&
        (filters[id][metric] === null || filters[id][metric]! < gate[key]!)
      )
        gateFailures.push(`${id}.${key}`);
    }
  }
  return {
    schemaVersion: 1,
    policyHash: digest(policy),
    policyVersion: policy.version,
    inferenceHash: inferenceHash(policy, model),
    datasetHash: digest(cases),
    requestedModel: model,
    total: cases.length,
    completed: cases.length - errors,
    errors,
    accuracy,
    reviewRate,
    confusion,
    filters,
    latencyMs: {
      mean: durations.reduce((a, b) => a + b, 0) / rows.length,
      p95: durations[Math.ceil(durations.length * 0.95) - 1],
    },
    usage,
    failedCaseIds,
    cases: rows,
    gateFailures,
    passed: errors === 0 && gateFailures.length === 0,
    reused,
  };
}
export interface EvaluationOptions {
  moderator?: Moderator;
  concurrency?: number;
  gates?: QualityGates;
  signal?: AbortSignal;
}
export async function evaluate(
  policy: PolicyConfig,
  cases: TestCase[],
  options: EvaluationOptions = {},
): Promise<EvaluationReport> {
  const moderator = options.moderator ?? new Moderator({ policy });
  const resolved = resolvePolicy(policy);
  if (digest(resolved) !== digest(moderator.policy))
    throw new ConfigurationError(
      "Moderator and evaluation policies must match",
    );
  validateCases(cases, resolved);
  validateGates(options.gates ?? { minAccuracy: 1 }, resolved);
  const concurrency = options.concurrency ?? 4;
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 64)
    throw new ConfigurationError("concurrency must be an integer from 1 to 64");
  const rows: CaseResult[] = new Array(cases.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(concurrency, cases.length) }, async () => {
      while (next < cases.length) {
        const index = next++,
          c = cases[index],
          start = performance.now();
        const row: CaseResult = {
          id: c.id,
          expectedAction: c.expectedAction,
          latencyMs: 0,
        };
        try {
          row.result = await moderator.moderate(c.message, c.history, {
            signal: options.signal,
          });
        } catch (error) {
          row.error = {
            code: error instanceof ModerationError ? error.code : "execution",
            message:
              error instanceof ModerationError
                ? error.message
                : "Evaluation failed",
          };
        }
        row.latencyMs = performance.now() - start;
        rows[index] = row;
      }
    }),
  );
  return summarize(resolved, moderator.model, cases, rows, options.gates);
}
/** Reuse only identical questions/model and dataset; a threshold change needs no inference. */
export function rethreshold(
  report: EvaluationReport,
  policy: PolicyConfig,
  cases: TestCase[],
  gates: QualityGates = { minAccuracy: 1 },
): EvaluationReport {
  const resolved = resolvePolicy(policy);
  validateCases(cases, resolved);
  if (
    report.inferenceHash !== inferenceHash(resolved, report.requestedModel) ||
    report.datasetHash !== digest(cases) ||
    report.cases.length !== cases.length
  )
    throw new ConfigurationError(
      "Replay requires identical questions, requested model, and dataset",
    );
  const rows = report.cases.map((row, i) => {
    if (row.id !== cases[i].id)
      throw new ConfigurationError("Replay case order mismatch");
    if (!row.result) return { ...row, latencyMs: 0 };
    const raw = mockResponse(
      Object.fromEntries(
        Object.entries(row.result.filters).map(([k, v]) => [k, v.probability]),
      ),
      row.result.model,
    );
    return {
      id: row.id,
      expectedAction: cases[i].expectedAction,
      latencyMs: 0,
      result: decideResult(resolved, raw),
    };
  });
  return summarize(resolved, report.requestedModel, cases, rows, gates, true);
}
export async function compare(
  left: PolicyConfig,
  right: PolicyConfig,
  cases: TestCase[],
  options: Omit<EvaluationOptions, "moderator"> & {
    leftModerator?: Moderator;
    rightModerator?: Moderator;
  } = {},
) {
  const a = options.leftModerator ?? new Moderator({ policy: left }),
    b = options.rightModerator ?? new Moderator({ policy: right });
  // Validate both before any billable request.
  const pa = resolvePolicy(left),
    pb = resolvePolicy(right);
  validateCases(cases, pa);
  validateCases(cases, pb);
  validateGates(options.gates ?? { minAccuracy: 1 }, pa);
  validateGates(options.gates ?? { minAccuracy: 1 }, pb);
  if (digest(a.policy) !== digest(pa) || digest(b.policy) !== digest(pb))
    throw new ConfigurationError(
      "Moderator and comparison policies must match",
    );
  const first = await evaluate(left, cases, { ...options, moderator: a });
  const second =
    inferenceHash(a.policy, a.model) === inferenceHash(b.policy, b.model)
      ? rethreshold(first, right, cases, options.gates)
      : await evaluate(right, cases, { ...options, moderator: b });
  return {
    left: first,
    right: second,
    reused: second.reused,
    changedCaseIds: cases
      .filter(
        (_, i) =>
          first.cases[i].result?.action !== second.cases[i].result?.action,
      )
      .map((c) => c.id),
    passed: first.passed && second.passed,
  };
}
