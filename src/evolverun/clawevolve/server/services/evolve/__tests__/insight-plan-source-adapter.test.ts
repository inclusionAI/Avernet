import { describe, expect, it } from "vitest";
import type { SessionEvidence } from "../../insight/contracts.js";
import { validateSessionEvidence } from "../../insight/evidence-validation.js";
import {
  buildInsightPlanSource,
  type InsightSourceRef,
} from "../adapters/insight-plan-source-adapter.js";
import {
  canonicalJson,
  digestJson,
  digestPlanSource,
  type PlanSource,
  validatePlanSource,
} from "../plan-source-contract.js";

const sourceRef: InsightSourceRef = {
  sourceRefVersion: "evolve-source-ref/v1",
  sourceType: "insight_improvement",
  improvement: {
    improvementId: 123,
    version: 4,
    title: "工具调用失败后没有降级",
    userGuidance: "先确认参数与权限，再提供替代路径",
    ownerUserId: "specialist-1",
    botOwnerUserId: "owner-1",
    botId: "bot-1",
    sourceType: "USER_SELECTED",
    sourceRuleId: null,
    dataStartTime: "2026-08-10T00:00:00Z",
    dataEndTime: "2026-08-11T00:00:00Z",
    dataAsOf: "2026-08-11T08:00:00Z",
    batchId: "batch-1",
    createdBy: "specialist-1",
    createdAt: "2026-08-11T08:30:00Z",
    updatedAt: "2026-08-11T08:30:00Z",
  },
  evidence: [{
    ordinal: 0,
    sessionId: "session-1",
    taskIndex: 2,
    taskDescription: "查询昨天的错误日志",
    failureClass: "PARAMETER_ERROR",
    reasoningSummary: "日志工具缺少时间范围",
    payloadRef: "oss://bucket/session-1.json",
    payloadEtag: "etag-1",
    payloadVersionId: "v1",
  }],
  evidenceRefsDigest: "sha256:frozen-ref-list",
  frozenAt: "2026-08-11T09:00:00Z",
};

const crossBotSourceRef: InsightSourceRef = {
  ...sourceRef,
  sourceRefVersion: "evolve-source-ref/v2",
  target: {
    ownerUserId: "specialist-1",
    botId: "target-bot-1",
    relationship: "cross_bot",
    selectedBy: "specialist-1",
    selectedAt: "2026-08-11T09:00:00Z",
    crossBotConfirmed: true,
  },
  targetRefDigest: digestJson({
    ownerUserId: "specialist-1",
    botId: "target-bot-1",
    relationship: "cross_bot",
    selectedBy: "specialist-1",
    selectedAt: "2026-08-11T09:00:00Z",
    crossBotConfirmed: true,
  }),
};

const evidence: SessionEvidence = {
  schema_version: "session-evidence/v1",
  batch_id: "batch-1",
  dt: "20260811",
  user_id: "owner-1",
  bot_id: "bot-1",
  session_id: "session-1",
  session: { start_time: "2026-08-11T07:00:00Z", opaque: { keep: true } },
  judge_meta: { judge_version: "v3" },
  generated_at: "2026-08-11T08:00:00Z",
  messages: [
    { message_index: 0, role: "user", timestamp: 1, visibility: "visible", content: "outside", raw: {} },
    { message_index: 1, role: "user", timestamp: 2, visibility: "visible", content: "查询昨天日志", raw: { trace: "keep-me" } },
    { message_index: 2, role: "assistant", timestamp: 3, visibility: "visible", content: "调用失败", raw: { tool_call: { args: {} } } },
    { message_index: 3, role: "assistant", timestamp: 4, visibility: "visible", content: "outside", raw: {} },
  ],
  tasks: [{
    task_index: 2,
    task_description: "查询昨天的错误日志",
    message_range: [1, 3],
    is_complete: 0,
    reasoning: "没有补齐参数",
    task_failure_class: "PARAMETER_ERROR",
    opaque_task_field: { keep: true },
  }],
};

describe("Insight plan source adapter", () => {
  it("accepts each frozen Evidence batch when the Improvement spans multiple batches", () => {
    const planSource = buildInsightPlanSource({
      ...sourceRef,
      improvement: { ...sourceRef.improvement, batchId: "MULTI_BATCH" },
    }, [
      { ...evidence, batch_id: "20260823_185341" },
    ]);

    expect(planSource.cases).toHaveLength(1);
    expect(planSource.cases[0]?.session_id).toBe("session-1");
    expect((planSource.cases[0]?.evidence as Record<string, unknown>)?.batch_id).toBe("20260823_185341");
  });

  it("uses the same canonical JSON digest vector as the Python resolver", () => {
    const vector = {
      z: 0.1,
      a: [1, true, null],
      "汉": "值",
      "\uE000": "private",
      "😀": "astral",
    };

    expect(canonicalJson(vector)).toBe(
      "{\"a\":[1e0,true,null],\"z\":1.0000000000000001e-1,\"汉\":\"值\",\"\":\"private\",\"😀\":\"astral\"}",
    );
    expect(digestJson(vector)).toBe(
      "sha256:0a9ed1f37826efcb7d2a734fa72a0b93b1b8af4dd7129aac0b73de7a7a4a014d",
    );
  });

  it("deterministically preserves the frozen target evidence without inventing a root cause", () => {
    const first = buildInsightPlanSource(sourceRef, [evidence]);
    const second = buildInsightPlanSource(sourceRef, [evidence]);

    expect(first).toEqual(second);
    expect(digestPlanSource(first)).toBe(digestPlanSource(second));
    expect(first.schema_version).toBe("plan-source/v2");
    expect(first.problem).toEqual({
      title: "工具调用失败后没有降级",
      user_guidance: "先确认参数与权限，再提供替代路径",
    });
    expect(first.cases).toHaveLength(1);
    expect(first.cases[0]).toEqual(expect.objectContaining({
      case_type: "bad",
      session_id: "session-1",
      task_index: 2,
      query: "查询昨天的错误日志",
      analysis: {
        failure_class: "PARAMETER_ERROR",
        evolution_failure_mode: "tool_parameter_error",
        judge_summary: "日志工具缺少时间范围",
      },
    }));
    expect(first.cases[0].analysis).not.toHaveProperty("root_cause_summary");
    expect(first.cases[0].evidence.messages).toEqual([
      expect.objectContaining({ message_index: 1, raw: { trace: "keep-me" } }),
      expect.objectContaining({ message_index: 2, raw: { tool_call: { args: {} } } }),
    ]);
    expect(first.cases[0].evidence.source_task).toEqual(expect.objectContaining({
      opaque_task_field: { keep: true },
    }));
    expect(first.analysis.root_cause_clusters).toEqual([]);
    expect(JSON.stringify(first)).not.toContain("agentMarkdown");
    expect(JSON.stringify(first)).not.toContain("IN_PROGRESS");
  });

  it("keeps Evidence provenance while exposing a separately confirmed execution target", () => {
    const result = buildInsightPlanSource(crossBotSourceRef, [evidence]);

    expect(result.source).toEqual(expect.objectContaining({
      adapter_version: "insight-to-plan-source/v2",
      owner_user_id: "specialist-1",
      bot_owner_user_id: "owner-1",
      bot_id: "bot-1",
    }));
    expect(result.planning_hints).toEqual({
      target_context: {
        relationship: "cross_bot",
        applicability_required: true,
        source_bot: { owner_user_id: "owner-1", bot_id: "bot-1" },
        execution_target: { owner_user_id: "specialist-1", bot_id: "target-bot-1" },
      },
    });
  });

  it("rejects evidence that no longer matches the frozen identity", () => {
    expect(() => buildInsightPlanSource(sourceRef, [{ ...evidence, bot_id: "other-bot" }]))
      .toThrow(/bot_id/);
  });

  it("rejects non-empty unmapped Evidence fields instead of silently dropping them", () => {
    const drifted = validateSessionEvidence({
      ...evidence,
      future_contract_field: { value: "must-not-disappear" },
    });

    expect(() => buildInsightPlanSource(sourceRef, [drifted])).toThrowError(expect.objectContaining({
      code: "PLAN_SOURCE_UNMAPPED_FIELD",
    }));
  });

  it("shares the v2 prospective case contract with Direct Goal producers", () => {
    const source = buildInsightPlanSource(sourceRef, [evidence]);
    const directGoal: PlanSource = {
      ...source,
      source: {
        type: "direct_goal",
        id: "direct-goal:EV-1",
        producer: "clawevolve-plan-direct-goal",
        bot_id: "bot-1",
        version: "2",
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
    };

    expect(() => validatePlanSource(directGoal)).not.toThrow();
    directGoal.cases[0].session_id = "fabricated-session";
    expect(() => validatePlanSource(directGoal)).toThrow(/session_id/);
  });
});
