import { describe, expect, it } from "vitest";
import { decideRecoveryPlan, observeRecovery, recoveryTaskResult, resumeRecoveryObservation, submitRecoveryPlan, terminateRecoveryTask } from "../task-state.js";
import type { RecoveryPlan, RecoveryTaskState } from "../task-contracts.js";
import { assertActionScope, engineActionScope } from "../action-scope.js";

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
    expect(() => submitRecoveryPlan(waiting, plan, 1000)).toThrow();
  });
  it("requires persisted evidence for recovery claims", () => {
    expect(() => observeRecovery(state(), { ...unknown, effect: "recovered", evidence_ids: ["made-up"] }, 100)).toThrow();
    expect(() => observeRecovery(state(), { ...unknown, effect: "recovered" }, 100)).toThrow();
  });
  it("puts Owner cooperation in a complete plan and binds approval to its digest", () => {
    const planned = submitRecoveryPlan({ ...state(), phase: "plan" }, plan, 100);
    expect(planned).toMatchObject({ status: "waiting_owner", ownerDeadlineAt: null, phase: "apply" });
    expect(() => decideRecoveryPlan(planned, "stale-plan", true, 200)).toThrow();
    const approved = decideRecoveryPlan(planned, planned.plan!.digest, true, 200);
    expect(approved.plan!.approvedAt).toBe(200);
    expect(approved.status).toBe("running");
  });
  it("does not request Owner decisions or grant writes in Debug", () => {
    const debug = submitRecoveryPlan({ ...state(), phase: "plan", level: "debug" }, plan, 100);
    expect(debug).toMatchObject({ status: "completed", ownerDeadlineAt: null, plan: { approvedAt: null } });
    expect(recoveryTaskResult(debug).effect).toBe("not_intervened");
  });
  it("reports a failed plan without entering a second repair cycle", () => {
    const failed = observeRecovery({ ...state(), phase: "verify", plan: { ...plan, digest: "digest", approvedAt: 1 } },
      { ...unknown, effect: "not_recovered", evidence_ids: ["E1"] }, 500);
    expect(failed.status).toBe("failed");
    expect(() => submitRecoveryPlan(failed, plan, 501)).toThrow();
  });
});

it("approves primary adjustments without requiring technical execution details", () => {
  const abstract: RecoveryPlan = { ...plan, actions: [{ actionId: "delete-duplicate", operationScope: { operation: "cron.delete", target: "duplicate" }, summary: "删除指定重复任务", risk: "保留正常任务", verification: "确认重复项已不存在", rollback: "恢复原配置" }] };
  const value = submitRecoveryPlan({ ...state(), phase: "plan" }, abstract, 100);
  expect(value.status).toBe("waiting_owner");
  expect(value.plan!.actions).toEqual(abstract.actions);
});

it.each(["enable", "disable"])("allows correcting a cron.%s plan before approval without changing the task", action => {
  const before = { ...state(), phase: "plan" as const };
  const actionPlan: RecoveryPlan = { ...plan, actions: [{ actionId: "toggle-cron", operationScope: { operation: `cron.${action}`, target: "98907" },
    summary: "调整指定定时任务的启用状态", risk: "需确认原停用原因", rollback: "恢复原状态", verification: "读取任务确认状态" }] };
  expect(() => submitRecoveryPlan(before, actionPlan, 100)).toThrowError(expect.objectContaining({
    status: 422, code: "invalid_recovery_result", message: expect.stringContaining("cron.update"),
  }));
  expect(before).toMatchObject({ status: "running", phase: "plan", version: 1, plan: null });
  actionPlan.actions[0].operationScope!.operation = "cron.update";
  actionPlan.actions[0].engineOperation = { method: "POST", path: `/api/v1/bot/cron/98907/${action}` };
  expect(submitRecoveryPlan(before, actionPlan, 100)).toMatchObject({
    status: "waiting_owner", phase: "apply", plan: { approvedAt: null, actions: actionPlan.actions },
  });
});

it("keeps a different action or target outside the approved scope forbidden", () => {
  const action = { actionId: "toggle-cron", operationScope: { operation: "cron.update", target: "98907" },
    summary: "启用指定任务", risk: "确认停用原因", verification: "读取任务状态" };
  for (const request of [{ method: "POST" as const, path: "/api/v1/bot/cron/other/enable" },
    { method: "DELETE" as const, path: "/api/v1/bot/cron/98907" }]) {
    expect(() => assertActionScope(action, request)).toThrowError(expect.objectContaining({ status: 403, code: "recovery_action_scope_mismatch" }));
  }
});


it("does not expose running or Owner-waiting states as upstream results", () => {
  expect(() => recoveryTaskResult(state())).toThrow();
  expect(() => recoveryTaskResult({ ...state(), status: "waiting_owner" })).toThrow();
});
it.each(["running", "waiting_owner", "waiting_effect"] as const)("terminates %s without changing evidence or authorizing continuation", status => {
  const before = { ...state(), status, executionRetry: { failures: 1, retryAt: 1000 }, judgment: unknown };
  const stopped = terminateRecoveryTask(before);
  expect(stopped).toMatchObject({ status: "canceled", observationDeadlineAt: null, ownerDeadlineAt: null, executionRetry: { retryAt: 0 } });
  expect(stopped.evidence).toEqual(before.evidence);
  expect(stopped.judgment).toEqual(before.judgment);
  expect(recoveryTaskResult(stopped)).toMatchObject({ status: "canceled", waiting_reason: null, effect: "unknown", result_text: expect.stringContaining("用户停止") });
  expect(() => resumeRecoveryObservation(stopped)).toThrow();
  expect(() => observeRecovery(stopped, unknown, 1000)).toThrow();
});


it.each([
  { operation: "mcp.create" }, { operation: "mcp.update", target: "server" }, { operation: "mcp.delete", target: "server" }, { operation: "skill.install" }, { operation: "skill.configure" }, { operation: "bot.configure" },
  { operation: "file.write", target: "/workspace/skills/review/SKILL.md" },
  { operation: "file.delete", target: "/workspace/skills-pool/skills-local/review" },
  { operation: "file.write", target: "/config/teclaw.json" },
])("requires Owner handling for protected changes %j", operationScope => {
  const action = { actionId: "change", operationScope, summary: "调整能力", risk: "影响任务", rollback: "撤销本次变更", verification: "读取并验证" };
  expect(() => submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan, actions: [action] }, 100)).toThrowError(expect.objectContaining({ status: 403 }));
  expect(submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan, actions: [] }, 100).status).toBe("waiting_owner");
});
it("rejects a prose-only action before approval rather than at execution", () => {
  expect(() => submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan,
    actions: [{ actionId: "change", summary: "修改参数", risk: "局部", verification: "确认生效", rollback: "恢复" }] }, 100)).toThrow(/operationScope/);
});
it("does not let a Shell MCP registration masquerade as an ordinary-file action", () => {
  const action = { actionId: "change", operationScope: { operation: "file.write", target: "/workspace/memory.md" },
    command: "mcporter config add maga https://example.invalid/mcp", summary: "调整工具", risk: "局部", verification: "核对配置", rollback: "撤销本次变更" };
  expect(() => submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan, actions: [action] }, 100)).toThrow(/Shell/);
});
it("does not approve existing-MCP deletion through Shell", () => {
  const action = { actionId: "delete", operationScope: { operation: "mcp.delete", target: "duplicate" }, command: "mcporter config remove duplicate",
    summary: "删除重复服务", risk: "保留其他服务", verification: "核对目标已删除", rollback: "恢复原配置" };
  expect(() => submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan, actions: [action] }, 100)).toThrow(/Owner/);
});
it("checks actual target_path when classifying and authorizing file deletion", () => {
  const action = { actionId: "delete", operationScope: { operation: "file.delete", target: "/workspace/duplicate.txt" },
    summary: "删除重复文件", risk: "仅目标文件", verification: "核对已删除" };
  const request = { method: "POST" as const, path: "/api/file/remove", body: { target_path: "/workspace/duplicate.txt" } };
  expect(engineActionScope(request)).toEqual(action.operationScope);
  expect(() => assertActionScope(action, request)).not.toThrow();
  expect(() => assertActionScope(action, { ...request, body: { target_path: "/workspace/skills/review" } })).toThrow();
});

it("requires Cron actions to use the adapter tool even when their business scope is approved", () => {
  const action = { actionId: "delete", operationScope: { operation: "cron.delete", target: "duplicate" }, command: "true",
    summary: "删除重复任务", risk: "保留其他任务", verification: "读取任务列表", rollback: "恢复原定义" };
  expect(() => submitRecoveryPlan({ ...state(), phase: "plan" }, { ...plan, actions: [action] }, 100)).toThrow(/Cron/);
});
