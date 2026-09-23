import { createHash } from "node:crypto";
import { Ajv } from "ajv";
import { TypeSafeClient, type NoulQuestion } from "@typesafe-ai/sdk";
import preset from "./data/community-v1.json" with { type: "json" };
import compiler from "./data/compiler.json" with { type: "json" };
import policySchema from "./data/policy.schema.json" with { type: "json" };
import caseSchema from "./data/case.schema.json" with { type: "json" };
import gatesSchema from "./data/gates.schema.json" with { type: "json" };

export type Action = "allow" | "review" | "block";
export interface FilterConfig {
  id: string;
  definition?: string;
  criteria?: { true: string; false: string };
  review?: number;
  block?: number;
  enabled?: boolean;
}
export interface PolicyConfig {
  schemaVersion: 1;
  version: string;
  mode: "extend" | "replace";
  preset?: "community-v1";
  instructions?: string;
  filters: FilterConfig[];
}
export interface Filter {
  id: string;
  definition: string;
  criteria?: { true: string; false: string };
  review: number;
  block: number;
}
export interface Policy {
  schemaVersion: 1;
  version: string;
  instructions: string;
  filters: Filter[];
}
export interface HistoryMessage {
  role: "user" | "assistant" | "system";
  content: string;
}
export interface TestCase {
  id: string;
  message: string;
  history?: HistoryMessage[];
  expectedAction: Action;
  labels?: Record<string, boolean>;
}
export interface QualityGates {
  minAccuracy?: number;
  maxReviewRate?: number;
  filters?: Record<string, { minPrecision?: number; minRecall?: number }>;
}
export interface Usage {
  input_tokens: number;
  output_tokens: number;
}
export interface FilterResult {
  probability: number;
  action: Action;
  reason: string;
}
export interface ModerationResult {
  action: Action;
  filters: Record<string, FilterResult>;
  matchedRuleIds: string[];
  policyVersion: string;
  policyHash: string;
  model: string;
  usage: Usage;
}
export interface Request {
  model: string;
  state: { target: string; history: HistoryMessage[] };
  questions: Record<string, NoulQuestion>;
}
export interface CallOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}
export interface DecisionClient {
  decide(request: Request, options: CallOptions): Promise<unknown>;
}
export class ModerationError extends Error {
  constructor(
    message: string,
    readonly code: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = new.target.name;
  }
}
export class ConfigurationError extends ModerationError {
  constructor(message: string) {
    super(message, "configuration");
  }
}
export class InputError extends ModerationError {
  constructor(message: string) {
    super(message, "input");
  }
}
export class ResponseError extends ModerationError {
  constructor(message = "Invalid model response") {
    super(message, "response");
  }
}
export class ProviderError extends ModerationError {
  constructor(cause: unknown) {
    super("TypeSafe request failed after configured retries", "provider", {
      cause,
    });
  }
}
export class CancelledError extends ModerationError {
  constructor() {
    super("Moderation cancelled", "cancelled");
  }
}
export class TimeoutError extends ModerationError {
  constructor() {
    super("Moderation deadline exceeded", "timeout");
  }
}
const ajv = new Ajv({ allErrors: true, strict: false });
const validators = {
  policy: ajv.compile(policySchema),
  case: ajv.compile(caseSchema),
  gates: ajv.compile(gatesSchema),
};
export function validateSchema(
  kind: keyof typeof validators,
  value: unknown,
): void {
  if (!validators[kind](value))
    throw new ConfigurationError(
      `Invalid ${kind}: ${ajv.errorsText(validators[kind].errors)}`,
    );
}
export function communityPolicy(): PolicyConfig {
  return structuredClone(preset) as PolicyConfig;
}
export function resolvePolicy(
  config: PolicyConfig = communityPolicy(),
): Policy {
  validateSchema("policy", config);
  if (config.mode === "replace" && config.preset)
    throw new ConfigurationError("preset is only valid in extend mode");
  const base = config.mode === "extend" ? communityPolicy() : undefined;
  const filters = new Map<string, FilterConfig>(
    (base?.filters ?? []).map((f) => [f.id, f]),
  );
  const seen = new Set<string>();
  for (const override of config.filters) {
    if (seen.has(override.id))
      throw new ConfigurationError(`Duplicate filter: ${override.id}`);
    seen.add(override.id);
    if (override.enabled === false) {
      if (!filters.has(override.id))
        throw new ConfigurationError(
          `Cannot disable unknown filter: ${override.id}`,
        );
      filters.delete(override.id);
    } else
      filters.set(override.id, { ...filters.get(override.id), ...override });
  }
  const resolved = [...filters.values()]
    .map((f) => {
      if (!f.definition?.trim())
        throw new ConfigurationError(`Missing definition: ${f.id}`);
      const review = f.review ?? 0.5,
        block = f.block ?? 0.9;
      if (review >= block)
        throw new ConfigurationError(`review must be less than block: ${f.id}`);
      return {
        id: f.id,
        definition: f.definition,
        ...(f.criteria ? { criteria: f.criteria } : {}),
        review,
        block,
      };
    })
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  if (!resolved.length)
    throw new ConfigurationError("At least one enabled filter is required");
  return structuredClone({
    schemaVersion: 1,
    version: config.version,
    instructions: config.instructions ?? base?.instructions ?? "",
    filters: resolved,
  });
}
// Hash a sorted JSON tree with numbers represented as IEEE-754 hex. This avoids
// language-dependent float rendering (1 vs 1.0, exponent formatting).
function hashTree(value: unknown): unknown {
  if (typeof value === "number") {
    const b = Buffer.alloc(8);
    b.writeDoubleBE(value === 0 ? 0 : value);
    return ["number", b.toString("hex")];
  }
  if (Array.isArray(value)) return ["array", value.map(hashTree)];
  if (value !== null && typeof value === "object")
    return [
      "object",
      Object.keys(value)
        .sort()
        .map((k) => [k, hashTree((value as Record<string, unknown>)[k])]),
    ];
  return value;
}
export function digest(value: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(hashTree(value)))
    .digest("hex");
}
export function compileQuestions(policy: Policy): Record<string, NoulQuestion> {
  return Object.fromEntries(
    policy.filters.map((f) => [
      f.id,
      {
        type: "noul",
        instructions: {
          task: compiler.instructions,
          policy: policy.instructions,
          rule: f.definition,
        },
        ...(f.criteria ? { criteria: f.criteria } : {}),
      },
    ]),
  );
}
export function inferenceHash(policy: Policy, model: string): string {
  return digest({ model, questions: compileQuestions(policy) });
}
export function validateCases(cases: TestCase[], policy: Policy): void {
  if (!Array.isArray(cases) || !cases.length)
    throw new ConfigurationError("Dataset must contain at least one case");
  const ids = new Set<string>(),
    rules = new Set(policy.filters.map((f) => f.id));
  for (const c of cases) {
    validateSchema("case", c);
    if (ids.has(c.id))
      throw new ConfigurationError(`Duplicate case ID: ${c.id}`);
    ids.add(c.id);
    for (const id of Object.keys(c.labels ?? {}))
      if (!rules.has(id)) throw new ConfigurationError(`Unknown label: ${id}`);
  }
}
export function makeRequest(
  policy: Policy,
  message: string,
  history: HistoryMessage[] = [],
  model = compiler.defaultModel,
): Request {
  try {
    validateSchema("case", {
      id: "input",
      message,
      history,
      expectedAction: "allow",
    });
  } catch {
    throw new InputError(
      "message must be nonempty text and history must contain role/content messages",
    );
  }
  const request: Request = {
    model,
    state: { target: message, history: structuredClone(history) },
    questions: compileQuestions(policy),
  };
  // A deliberately conservative UTF-8 byte ceiling, NOT a claim of exact tokenization.
  if (Buffer.byteLength(JSON.stringify(request)) > compiler.maxRequestBytes)
    throw new InputError(
      `Request exceeds ${compiler.maxRequestBytes} UTF-8 bytes; shorten content or policy explicitly`,
    );
  return request;
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
export function decideResult(policy: Policy, raw: unknown): ModerationResult {
  if (
    !record(raw) ||
    typeof raw.model !== "string" ||
    !raw.model.trim() ||
    !record(raw.answers) ||
    !record(raw.usage)
  )
    throw new ResponseError();
  for (const key of ["input_tokens", "output_tokens"])
    if (!Number.isSafeInteger(raw.usage[key]) || (raw.usage[key] as number) < 0)
      throw new ResponseError("Invalid token usage");
  const filters: Record<string, FilterResult> = {};
  let action: Action = "allow";
  for (const f of policy.filters) {
    const a = raw.answers[f.id];
    if (
      !record(a) ||
      a.type !== "noul" ||
      typeof a.noul !== "number" ||
      !Number.isFinite(a.noul) ||
      a.noul < 0 ||
      a.noul > 1
    )
      throw new ResponseError(`Missing or invalid Noul answer: ${f.id}`);
    const decision: Action =
      a.noul >= f.block ? "block" : a.noul >= f.review ? "review" : "allow";
    filters[f.id] = {
      probability: a.noul,
      action: decision,
      reason: f.definition,
    };
    if (decision === "block" || (decision === "review" && action === "allow"))
      action = decision;
  }
  return {
    action,
    filters,
    matchedRuleIds: Object.keys(filters).filter(
      (k) => filters[k].action !== "allow",
    ),
    policyVersion: policy.version,
    policyHash: digest(policy),
    model: raw.model,
    usage: raw.usage as unknown as Usage,
  };
}
export class TypeSafeAdapter implements DecisionClient {
  constructor(
    readonly client: TypeSafeClient = new TypeSafeClient({ logLevel: "off" }),
  ) {}
  async decide(request: Request, options: CallOptions): Promise<unknown> {
    // Raw response avoids upstream parsing/coercion hiding malformed or missing answers.
    const response = await this.client
      .systemOne(
        {
          ...request,
          state: {
            target: request.state.target,
            history: request.state.history.map((m) => ({ ...m })),
          },
        },
        { signal: options.signal, timeout: options.timeoutMs },
      )
      .asResponse();
    try {
      return await response.json();
    } catch {
      throw new ResponseError("Model response is not JSON");
    }
  }
}
export interface ModeratorOptions {
  policy?: PolicyConfig;
  model?: string;
  apiKey?: string;
  client?: DecisionClient;
  timeoutMs?: number;
}
export class Moderator {
  private readonly resolved: Policy;
  readonly model: string;
  readonly timeoutMs: number;
  private client?: DecisionClient;
  private readonly apiKey?: string;
  constructor(options: ModeratorOptions = {}) {
    this.resolved = resolvePolicy(options.policy);
    this.model = options.model ?? compiler.defaultModel;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    if (typeof this.model !== "string" || !this.model.trim())
      throw new ConfigurationError("model must be nonempty");
    if (!Number.isFinite(this.timeoutMs) || this.timeoutMs <= 0)
      throw new ConfigurationError("timeoutMs must be positive");
    this.client = options.client;
    this.apiKey = options.apiKey;
  }
  get policy(): Policy {
    return structuredClone(this.resolved);
  }
  async moderate(
    message: string,
    history: HistoryMessage[] = [],
    options: CallOptions = {},
  ): Promise<ModerationResult> {
    const request = makeRequest(this.resolved, message, history, this.model);
    if (options.signal?.aborted) throw new CancelledError();
    const timeout = options.timeoutMs ?? this.timeoutMs;
    if (!Number.isFinite(timeout) || timeout <= 0)
      throw new ConfigurationError("timeoutMs must be positive");
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort: (() => void) | undefined;
    try {
      this.client ??= new TypeSafeAdapter(
        new TypeSafeClient({ apiKey: this.apiKey, logLevel: "off" }),
      );
      const stopped = new Promise<never>((_, reject) => {
        abort = () => {
          controller.abort();
          reject(new CancelledError());
        };
        options.signal?.addEventListener("abort", abort, { once: true });
        timer = setTimeout(() => {
          controller.abort();
          reject(new TimeoutError());
        }, timeout);
      });
      const raw = await Promise.race([
        this.client.decide(request, {
          signal: controller.signal,
          timeoutMs: timeout,
        }),
        stopped,
      ]);
      return decideResult(this.resolved, raw);
    } catch (error) {
      if (error instanceof ModerationError) throw error;
      throw new ProviderError(error);
    } finally {
      clearTimeout(timer);
      if (abort) options.signal?.removeEventListener("abort", abort);
    }
  }
}
/** Queue of raw responses/errors; exhaustion is an error, never an implicit allow. */
export class MockClient implements DecisionClient {
  readonly requests: Request[] = [];
  private readonly responses: unknown[];
  constructor(responses: unknown[]) {
    this.responses = [...responses];
  }
  async decide(request: Request): Promise<unknown> {
    this.requests.push(structuredClone(request));
    if (!this.responses.length)
      throw new Error("Mock response queue exhausted");
    const result = this.responses.shift();
    if (result instanceof Error) throw result;
    return structuredClone(result);
  }
}
export function mockResponse(
  probabilities: Record<string, number>,
  model = compiler.defaultModel,
): unknown {
  return {
    model,
    answers: Object.fromEntries(
      Object.entries(probabilities).map(([id, noul]) => [
        id,
        { type: "noul", noul },
      ]),
    ),
    usage: { input_tokens: 0, output_tokens: 0 },
  };
}
