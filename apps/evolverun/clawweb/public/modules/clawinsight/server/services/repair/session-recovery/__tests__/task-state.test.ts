import { describe, expect, it } from "vitest";
import { decideRecoveryPlan, observeRecovery, recoveryTaskResult, resumeRecoveryObservation, submitRecoveryPlan } from "../task-state.js";
import type { RecoveryPlan, RecoveryTaskState } from "../task-contracts.js";

function state(): RecoveryTaskState {
  return { schemaVersion: "session-recovery-task/v1", taskId: "SR-1", level: "approval", status: "running",
    phase: "observe", version: 1, observationDeadlineAt: 1000, ownerDeadlineAt: null,
    plan: null, judgment: null, evidence: [{ evidence_id: "E1", tool_call_id: "TC1", source: "session", location: "message:1", excerpt: "原操作失败" }],
    request: { event_id: "event1", tc_fault_label: null, delivery_key: "delivery1", diagnosis: "原操作失败",
      target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" }, session_key: "agent:main:session:1" } };
}
const plan: RecoveryPlan = { summary: "修复环境", basis: "原操作失败", scope: "当前目标配置", verification: "原会话操作成功",
  actions: [], owner_cooperation: [{ request: "确认权限", basis: "权限未开放" }] };
const unknown = { delivery: "unconfirmed" as const, response: "unknown" as const, effect: "unknown" as const, summary: "尚无足够证据", evidence_ids: [] };

describe("session recovery business lifecycle", () => {
  it("times out into effect waiting and resumes the original verification stage", () => {
    const verifying = { ...state(), phase: "verify" as const };
    const waiting = observeRecovery(verifying, unknown, 1000);
    expect(waiting).toMatchObject({ status: "waiting_effect", phase: "verify" });
    const resumed = resumeRecoveryObservation(waiting);
    expect(resumed.phase).toBe("verify");
    expect(resumed.observationDeadlineAt).toBe(1000);
    expect(recoveryTaskResult(waiting)).toMatchObject({ waiting_reason: "effect", effect: "unknown" });
  });
  it("does not treat observation timeout as permission to repair", () => {
    const waiting = observeRecovery(state(), unknown, 1000);
    expect(() => submitRecoveryPlan(waiting, plan, 1000, 2000)).toThrow();
  });
  it("requires persisted evidence for recovery claims", () => {
    expect(() => observeRecovery(state(), { ...unknown, effect: "recovered", evidence_ids: ["made-up"] }, 100)).toThrow();
    expect(() => observeRecovery(state(), { ...unknown, effect: "recovered" }, 100)).toThrow();
  });
  it("puts Owner cooperation in a complete plan and binds approval to its digest", () => {
    const planned = submitRecoveryPlan({ ...state(), phase: "plan" }, plan, 100, 2000);
    expect(planned).toMatchObject({ status: "waiting_owner", ownerDeadlineAt: 2100, phase: "apply" });
    expect(() => decideRecoveryPlan(planned, "stale-plan", true, 200)).toThrow();
    const approved = decideRecoveryPlan(planned, planned.plan!.digest, true, 200);
    expect(approved.plan!.approvedAt).toBe(200);
    expect(approved.status).toBe("running");
  });
  it("does not request Owner decisions or grant writes in Debug", () => {
    const debug = submitRecoveryPlan({ ...state(), phase: "plan", level: "debug" }, plan, 100, 2000);
    expect(debug).toMatchObject({ status: "completed", ownerDeadlineAt: null, plan: { approvedAt: null } });
    expect(recoveryTaskResult(debug).effect).toBe("not_intervened");
  });
  it("reports a failed plan without entering a second repair cycle", () => {
    const failed = observeRecovery({ ...state(), phase: "verify", plan: { ...plan, digest: "digest", approvedAt: 1 } },
      { ...unknown, effect: "not_recovered", evidence_ids: ["E1"] }, 500);
    expect(failed.status).toBe("failed");
    expect(() => submitRecoveryPlan(failed, plan, 501, 1000)).toThrow();
  });
});

it("approves primary adjustments without requiring technical execution details", () => {
  const abstract: RecoveryPlan = { ...plan, actions: [{ actionId: "delete-duplicate", summary: "删除指定重复任务", risk: "保留正常任务", verification: "确认重复项已不存在", rollback: "恢复原配置" }] };
  const value = submitRecoveryPlan({ ...state(), phase: "plan" }, abstract, 100, 2000);
  expect(value.status).toBe("waiting_owner");
  expect(value.plan!.actions).toEqual(abstract.actions);
});
