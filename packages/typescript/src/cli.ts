#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import { parseArgs } from "node:util";
import {
  communityPolicy,
  resolvePolicy,
  validateCases,
  Moderator,
  ModerationError,
  type PolicyConfig,
  type TestCase,
} from "./core.js";
import { evaluate, compare, validateGates } from "./evaluation.js";

async function main(): Promise<number> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(2),
    allowPositionals: true,
    options: {
      policy: { type: "string" },
      against: { type: "string" },
      cases: { type: "string" },
      gates: { type: "string" },
      output: { type: "string" },
      concurrency: { type: "string", default: "4" },
      model: { type: "string" },
      "timeout-ms": { type: "string", default: "30000" },
      help: { type: "boolean" },
    },
  });
  if (values.help) {
    console.log(
      "jev-moderation validate|eval|compare --cases cases.jsonl [--policy policy.json] [--against other.json] [--gates gates.json] [--output report.json] [--concurrency 4] [--model jev-1.13.0] [--timeout-ms 30000]",
    );
    return 0;
  }
  const command = positionals[0];
  if (
    positionals.length !== 1 ||
    !["validate", "eval", "compare"].includes(command) ||
    !values.cases
  )
    throw new Error(
      "Specify validate, eval, or compare and --cases; use --help",
    );
  const read = (p: string) => JSON.parse(readFileSync(p, "utf8"));
  const policy: PolicyConfig = values.policy
    ? read(values.policy)
    : communityPolicy();
  const cases: TestCase[] = readFileSync(values.cases, "utf8")
    .split(/\r?\n/)
    .filter((s) => s.trim())
    .map((s, i) => {
      try {
        return JSON.parse(s);
      } catch {
        throw new Error(`Invalid JSONL record ${i + 1}`);
      }
    });
  const gates = values.gates ? read(values.gates) : { minAccuracy: 1 };
  const resolved = resolvePolicy(policy);
  validateCases(cases, resolved);
  validateGates(gates, resolved);
  if (command === "validate") {
    console.log(
      `Valid: ${resolved.filters.length} filters, ${cases.length} cases`,
    );
    return 0;
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.once("SIGINT", cancel);
  try {
    const make = (p: PolicyConfig) =>
      new Moderator({
        policy: p,
        model: values.model,
        timeoutMs: Number(values["timeout-ms"]),
      });
    const options = {
      concurrency: Number(values.concurrency),
      gates,
      signal: controller.signal,
    };
    let report;
    if (command === "compare") {
      if (!values.against) throw new Error("compare requires --against");
      const other = read(values.against);
      report = await compare(policy, other, cases, {
        ...options,
        leftModerator: make(policy),
        rightModerator: make(other),
      });
    } else
      report = await evaluate(policy, cases, {
        ...options,
        moderator: make(policy),
      });
    if (values.output)
      writeFileSync(values.output, JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report, null, 2));
    const reports = "left" in report ? [report.left, report.right] : [report];
    console.error(
      reports
        .map(
          (r) =>
            `${r.policyVersion}: ${r.completed}/${r.total} completed; accuracy=${r.accuracy.toFixed(3)} review=${r.reviewRate.toFixed(3)} errors=${r.errors}; gates=${r.gateFailures.join(",") || "pass"}`,
        )
        .join("\n"),
    );
    return reports.some((r) => r.errors) ? 2 : report.passed ? 0 : 1;
  } finally {
    process.removeListener("SIGINT", cancel);
  }
}
main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(
      error instanceof ModerationError
        ? `${error.code}: ${error.message}`
        : error instanceof Error
          ? error.message
          : "CLI failed",
    );
    process.exitCode = 2;
  });
