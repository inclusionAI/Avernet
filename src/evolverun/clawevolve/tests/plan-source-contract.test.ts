import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalJson,
  digestJson,
  digestPlanSource,
  PLAN_SOURCE_SCHEMA_VERSION,
  type PlanSource,
  validatePlanSource,
} from "../src/server/services/evolve/plan-source-contract.js";

function prospectiveSource(): PlanSource {
  return {
    schema_version: PLAN_SOURCE_SCHEMA_VERSION,
    generated_at: "2026-08-11T09:00:00Z",
    source: {
      type: "direct_goal",
      id: "direct-goal:EV-1",
      producer: "public-plan-producer",
      bot_id: "bot-1",
      version: "2",
    },
    problem: {
      title: "验证未来能力",
      user_guidance: null,
    },
    cases: [{
      case_id: "goal-case-1",
      case_type: "prospective",
      query: "验证未来能力",
      context: { scenario: "new capability" },
      evidence: { items: ["explicit user goal"] },
      analysis: { evolution_failure_mode: "missing_skill_capability" },
      planning_hints: { success_criteria: ["能力可执行"] },
    }],
    analysis: {
      case_distribution: { prospective: 1 },
      root_cause_clusters: [],
    },
    planning_hints: {},
    extensions: {},
  };
}

test("keeps the canonical JSON digest compatible across runtimes", () => {
  const vector = {
    z: 0.1,
    a: [1, true, null],
    "汉": "值",
    "\uE000": "private",
    "😀": "astral",
  };

  assert.equal(
    canonicalJson(vector),
    "{\"a\":[1e0,true,null],\"z\":1.0000000000000001e-1,\"汉\":\"值\",\"\":\"private\",\"😀\":\"astral\"}",
  );
  assert.equal(
    digestJson(vector),
    "sha256:0a9ed1f37826efcb7d2a734fa72a0b93b1b8af4dd7129aac0b73de7a7a4a014d",
  );
});

test("validates and deterministically digests prospective plan sources", () => {
  const source = prospectiveSource();
  assert.doesNotThrow(() => validatePlanSource(source));
  assert.equal(digestPlanSource(source), digestPlanSource(structuredClone(source)));
});

test("rejects fabricated sessions for prospective cases", () => {
  const source = prospectiveSource();
  source.cases[0].session_id = "fabricated-session";
  assert.throws(() => validatePlanSource(source), /session_id/);
});

test("rejects duplicate case identifiers", () => {
  const source = prospectiveSource();
  source.cases.push({ ...source.cases[0] });
  assert.throws(() => validatePlanSource(source), /case_id 重复/);
});
