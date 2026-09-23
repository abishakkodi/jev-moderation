import { expect, test } from "vitest";
import {
  Moderator,
  MockClient,
  mockResponse,
  evaluate,
  type PolicyConfig,
} from "jev-moderation";

const policy: PolicyConfig = {
  schemaVersion: 1,
  version: "ads-v1",
  mode: "replace",
  filters: [{ id: "ads", definition: "Unsolicited promotion of a product." }],
};

test("application routes flagged messages to review", async () => {
  const moderator = new Moderator({
    policy,
    client: new MockClient([mockResponse({ ads: 0.7 })]),
  });
  expect((await moderator.moderate("Buy my product")).action).toBe("review");
});

// Explicit opt-in: TYPESAFE_LIVE_EVAL=1 plus TYPESAFE_API_KEY.
// This performs billable inference; a model classification failure is meaningful.
test.skipIf(process.env.TYPESAFE_LIVE_EVAL !== "1")(
  "custom filter quality on labeled examples",
  async () => {
    const report = await evaluate(
      policy,
      [
        {
          id: "ad",
          message: "Buy my unrelated product now!",
          expectedAction: "block",
          labels: { ads: true },
        },
        {
          id: "thanks",
          message: "Thanks for your help!",
          expectedAction: "allow",
          labels: { ads: false },
        },
      ],
      { gates: { minAccuracy: 1 } },
    );
    expect(report.passed, JSON.stringify(report)).toBe(true);
  },
);
