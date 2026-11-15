import { describe, expect, it } from "vitest";
import { confirmOwnerRequest, requestOwnerCooperation } from "../owner-cooperation.js";
import { decideRecoveryPlan, recoveryTaskResult } from "../task-state.js";
import type { RecoveryTaskState } from "../task-contracts.js";

function task(): RecoveryTaskState {
  return { schemaVersion: "session-recovery-task/v1", taskId: "SR-one", version: 1, level: "approval",
    status: "running", phase: "observe", controlPlaneEnvironment: "pre", observationDeadlineAt: 100,
    ownerDeadlineAt: null, plan: null, judgment: null,
    request: { event_id: "event", delivery_key: "initial", tc_fault_label: null, diagnosis: "缺文档权限",
      session_key: "original", target: { entity_id: "owner", bot_id: "bot", env: "prod", engine: "OC" } },
    evidence: [{ evidence_id: "e1", tool_call_id: "read", source: "session_read", location: "line:4", excerpt: "原文档拒绝访问" }] };
}
const input = { reason: "permission" as const, summary: "评审缺文档权限", instructions: "请申请原文档读取权限。", evidence_ids: ["e1"] };

describe("Owner cooperation independent of environment approval", () => {
  it("waits without making a plan, claiming recovery, or reporting a final upstream result", () => {
    const waiting = requestOwnerCooperation(task(), input, "request-1", 10);
    expect(waiting).toMatchObject({ status: "waiting_owner", phase: "observe", plan: null, observationDeadlineAt: null,
      ownerRequest: { requestId: "request-1", revision: 1, status: "pending", resumePhase: "observe", ...input } });
    expect(() => recoveryTaskResult(waiting)).toThrow();
    expect(() => decideRecoveryPlan(waiting, "request-1", true, 20)).toThrow();
  });
  it("re-enters assessment for verification without treating the Owner claim as recovery or approval", () => {
    const waiting = requestOwnerCooperation(task(), input, "request-1", 10);
    const saved = confirmOwnerRequest(waiting, "request-1", "owner", "已开通权限", 20);
    expect(saved).toMatchObject({ status: "running", phase: "assess", requiresFreshObservation: true, plan: null, judgment: null,
      ownerRequest: { status: "confirmed", feedback: { userId: "owner", text: "已开通权限", atMs: 20 } } });
    expect(confirmOwnerRequest(saved, "request-1", "owner", "重复平台投递", 30)).toBe(saved);
  });
  it("retains an existing plan and resumes with no additional environment authorization", () => {
    const original = { ...task(), phase: "verify" as const, plan: { summary: "原方案", basis: "b", scope: "s", verification: "v", actions: [], owner_cooperation: [], digest: "approved", approvedAt: 1 } };
    const confirmed = confirmOwnerRequest(requestOwnerCooperation(original, input, "r1", 10), "r1", "owner", "", 20);
    expect(confirmed.plan).toEqual(original.plan);
    expect(confirmed.ownerRequest?.resumePhase).toBe("verify");
    expect(() => requestOwnerCooperation(confirmed, input, "r2", 30)).toThrow();
    const partial = requestOwnerCooperation({ ...confirmed, requiresFreshObservation: false }, { ...input, instructions: "还缺写权限" }, "r2", 30);
    expect(partial.ownerRequest?.resumePhase).toBe("verify");
  });
  it("reuses one card identity while replacing a partially fulfilled request with a new revision", () => {
    const waiting = requestOwnerCooperation(task(), input, "r1", 10);
    const confirmed = confirmOwnerRequest(waiting, "r1", "owner", "", 20);
    const next = requestOwnerCooperation({ ...confirmed, requiresFreshObservation: false }, { ...input, instructions: "仍缺文档写权限" }, "r2", 30);
    expect(next.ownerRequest).toMatchObject({ revision: 2, requestId: "r2", status: "pending", instructions: "仍缺文档写权限" });
    expect(() => confirmOwnerRequest(next, "r1", "owner", "", 40)).toThrow();
  });
  it.each(["completed", "failed", "canceled"] as const)("does not reactivate a %s task from an old card", status => {
    const pending = requestOwnerCooperation(task(), input, "r1", 10);
    expect(() => confirmOwnerRequest({ ...pending, status }, "r1", "owner", "", 20)).toThrow();
  });
  it("requires a supported reason, concrete actions and saved controlled evidence", () => {
    for (const change of [{ reason: "network" }, { reason: "materials" }, { instructions: "" }, { evidence_ids: ["invented"] }]) {
      expect(() => requestOwnerCooperation(task(), { ...input, ...change } as typeof input, "r1", 10)).toThrow();
    }
    expect(() => requestOwnerCooperation({ ...task(), level: "debug" }, input, "r1", 10)).toThrow();
    expect(() => requestOwnerCooperation({ ...task(), phase: "apply" }, input, "r1", 10)).toThrow();
  });
});
