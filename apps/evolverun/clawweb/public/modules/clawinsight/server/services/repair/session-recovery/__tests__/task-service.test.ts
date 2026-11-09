import { recoveryTargetSnapshot } from "../agent-service.js";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionRecoveryError } from "../errors.js";
import { DingTalkCardNotDispatchedError } from "../../../notifications/dingtalk-sender.js";
import type { RecoveryStoredTask, RecoveryTaskStore } from "../task-contracts.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { parseRequest } from "../validation.js";
import { logRepairEvent } from "../../common/logging.js";
vi.mock("../../common/logging.js", async importOriginal => ({
  ...await importOriginal<typeof import("../../common/logging.js")>(), logRepairEvent: vi.fn(),
}));
const request = { event_id: "evt", tc_fault_label: "TC.MCP.CALL", delivery_key: "send1", diagnosis: "操作失败",
  session_key: "agent:main:session:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } };
const actor = { userId: "owner", isAdmin: false };
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
afterEach(() => vi.restoreAllMocks());
function setup(callbackFails = false, now = () => 100) {
  const records = new Map<string, RecoveryStoredTask>();
  const store: RecoveryTaskStore = {
    create: async value => { if (records.has(value.taskId)) throw new Error("duplicate"); records.set(value.taskId, structuredClone(value)); },
    find: async id => records.has(id) ? structuredClone(records.get(id)!) : null,
    listForEvent: async (eventId, ownerId, isAdmin) => [...records.values()].filter(value => value.request.event_id === eventId && (isAdmin || value.request.target.entity_id === ownerId || value.shared === true)),
    listForReporting: async (limit, afterTaskId = "") => ({ tasks: [...records.values()].filter(value => value.taskId > afterTaskId && value.pending_results.length > 0).sort((a, b) => a.taskId.localeCompare(b.taskId)).slice(0, limit), nextTaskId: "" }),
    listForMaintenance: async (limit, afterTaskId = "") => [...records.values()].filter(value => value.taskId > afterTaskId).slice(0, limit),
    update: async (id, version, change) => {
      const value = records.get(id); if (!value || value.version !== version) return null;
      const changed = change(structuredClone(value)); records.set(id, structuredClone(changed)); return changed;
    },
    acknowledgeResult: async (id, version) => { const value = records.get(id)!; value.pending_results = value.pending_results.filter(item => item.result_version !== version); },
    claimResult: async (id, version) => records.get(id)?.pending_results[0]?.result_version === version ? 1 : null,
    failResult: async () => {},
    appendNotification: async (id, event) => { const value = records.get(id)!; if (value.timeline.some(item => item.event_id === event.event_id)) return false; value.timeline.push(event); return true; },
  };
  const delivery = { create: vi.fn().mockResolvedValue({ event_id: "evt", delivery_key: "send1", status: "accepted" }) };
  const agent = { start: vi.fn().mockResolvedValue(undefined), release: vi.fn().mockResolvedValue(undefined), listExecutions: vi.fn().mockResolvedValue([]),
    terminate: vi.fn().mockResolvedValue({ status: "remote_stopped", aisJobId: "job" }) };
  const notifier = { notify: vi.fn().mockResolvedValue(undefined) };
  const reporter = { report: callbackFails ? vi.fn().mockRejectedValue(new Error("callback offline")) : vi.fn().mockResolvedValue(undefined) };
  const deps: ConstructorParameters<typeof SessionRecoveryTaskService>[0] = { controlPlaneEnvironment: "pre", tasks: store, agent, notifier, reporter,
    observationMs: 3600000, now };
  return { records, delivery, agent, notifier, reporter, deps, service: new SessionRecoveryTaskService(deps) };
}

describe("self-healing task orchestration", () => {
  it.each(["none", "unknown", "performed", "accepted", "unknown_delivery", "missing_read"] as const)(
    "closes an assessment as unnecessary only with verified evidence and no possible intervention (%s)", async scenario => {
      const { service, records, agent } = setup();
      const { task_id } = await service.create(request, actor);
      const task = records.get(task_id)!;
      task.interventionStatus = scenario === "unknown" || scenario === "performed" ? scenario : "none";
      if (scenario === "accepted" || scenario === "unknown_delivery") {
        task.delivery = { delivery: { status: scenario === "accepted" ? "accepted" : "unknown" } } as NonNullable<RecoveryStoredTask["delivery"]>;
      }
      task.evidence.push({ evidence_id: "read", tool_call_id: "read-call", source: "session_read",
        location: "original session", excerpt: "等待审批人确认" });
      if (scenario !== "missing_read") {
        task.timeline.push({ event_id: "read-call", at_ms: 10, kind: "operation", actor: "agent",
          summary: "检查原会话", payload: { raw_tool_call_id: "read-call", status: "succeeded" } });
      }
      const assessed = service.assess(task_id, task.version, { decision: "no_recovery_needed",
        summary: "原会话按业务规则等待审批人确认", evidence_ids: ["read"] });

      if (scenario !== "none") {
        await expect(assessed).rejects.toMatchObject({ status: 422 });
        expect(records.get(task_id)).toMatchObject({ status: "running", phase: "assess", pending_results: [] });
        expect(agent.release).not.toHaveBeenCalled();
        return;
      }
      await assessed;
      expect(records.get(task_id)).toMatchObject({
        status: "completed", phase: "assess", completionReason: "no_recovery_needed",
        judgment: { effect: "unknown", evidence_ids: ["read"] },
        pending_results: [{ status: "completed", completion_reason: "no_recovery_needed", intervention_status: "none", effect: "unknown" }],
      });
      expect(agent.release).toHaveBeenCalledOnce();
    });
  it("retries card creation if the first request never left the notifier", async () => {
    let now = 40000;
    const { service, records, deps } = setup(false, () => now);
    const { task_id } = await service.create(request, actor); await flush();
    const task = records.get(task_id)!;
    task.status = "waiting_owner";
    task.ownerRequest = { requestId: "r1", revision: 1, status: "pending", reason: "permission", summary: "权限", instructions: "申请权限", evidence_ids: [], resumePhase: "assess", createdAtMs: 1 };
    const notify = vi.fn().mockRejectedValueOnce(new DingTalkCardNotDispatchedError(new Error("token unavailable"))).mockResolvedValueOnce(true);
    deps.notifier.notifyOwnerRequest = notify;
    await service.maintenance(); await flush();
    expect(records.get(task_id)?.timeline.at(-1)?.payload).toMatchObject({ operation: "create", dispatched: false });

    now += 61000;
    await service.maintenance(); await flush();
    expect(notify.mock.calls.map(([input]) => input.updateOnly)).toEqual([false, false]);
    expect(records.get(task_id)?.timeline.at(-1)?.payload).toMatchObject({ operation: "create", state: "sent" });
  });

  it("reconciles a crashed card send with bounded updates of the same instance, retaining unknown delivery", async () => {
    let now = 40000;
    const { service, records, deps } = setup(false, () => now);
    const { task_id } = await service.create(request, actor); await flush();
    const task = records.get(task_id)!;
    task.status = "waiting_owner";
    task.ownerRequest = { requestId: "r1", revision: 1, status: "pending", reason: "permission", summary: "权限", instructions: "申请权限", evidence_ids: [], resumePhase: "assess", createdAtMs: 1 };
    const key = "owner-card-r1-pending-waiting_owner-assess";
    task.timeline.push({ event_id: `${key}-0`, at_ms: 1, kind: "notification", actor: "service", summary: "发送中", payload: { channel: "owner_card", notification_key: key, state: "sending", operation: "create", owner_request_id: "r1" } });
    const notify = vi.fn().mockResolvedValue(true); deps.notifier.notifyOwnerRequest = notify;
    await service.maintenance(); await flush();
    expect(notify).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ updateOnly: true }));
    expect(records.get(task_id)?.timeline.at(-1)?.payload).toMatchObject({ state: "unconfirmed", sent: false, synced: true });
    await service.maintenance(); await flush(); expect(notify).toHaveBeenCalledOnce();
    now = 200000; await service.maintenance(); await flush(); expect(notify).toHaveBeenCalledTimes(2);
    now = 400000; await service.maintenance(); await flush(); expect(notify).toHaveBeenCalledTimes(2);
  });
  it("repairs a late old card write with the latest request instead of leaving a stale button", async () => {
    const { service, records, deps } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    const task = records.get(task_id)!; task.status = "waiting_owner";
    task.ownerRequest = { requestId: "r1", revision: 1, status: "pending", reason: "permission", summary: "文档权限不足", instructions: "请申请原文档权限", evidence_ids: [], resumePhase: "assess", createdAtMs: 1 };
    let finishOld!: (value: boolean) => void;
    const notify = vi.fn().mockImplementationOnce(() => new Promise<boolean>(resolve => { finishOld = resolve; })).mockResolvedValue(true);
    deps.notifier.notifyOwnerRequest = notify;
    await service.maintenance(); await flush();
    records.get(task_id)!.ownerRequest = { ...task.ownerRequest, requestId: "r2", revision: 2, instructions: "仍缺写权限" };
    await service.maintenance(); await flush();
    finishOld(true); await flush();
    expect(notify).toHaveBeenCalledTimes(3);
    expect(notify.mock.calls[2][0]).toMatchObject({ updateOnly: true, task: { ownerRequest: { requestId: "r2", instructions: "仍缺写权限" } } });
  });
  it("saves Owner feedback before asynchronously waking the same task, with Owner and control-plane checks", async () => {
    const { service, records, agent, deps } = setup();
    const { task_id } = await service.create(request, actor); await flush(); agent.start.mockClear();
    const task = records.get(task_id)!;
    task.execution = { executionId: "exec", stepId: "step", ticketDigest: "digest", jobId: "job", ccSessionId: null,
      state: "running", leaseExpiresAt: 1000, decisionDeadlineAt: null, lastHeartbeatAt: 1, invalidatedAt: null };
    task.evidence = [{ evidence_id: "read", tool_call_id: "call", source: "session_read", location: "original", excerpt: "缺文档权限" }];
    await service.requestOwner(task_id, task.version, { reason: "permission", summary: "缺文档权限", instructions: "申请原文档读取权限", evidence_ids: ["read"], executionId: "exec" });
    const pending = records.get(task_id)!;
    expect(pending).toMatchObject({ status: "waiting_owner", plan: null, pending_results: [] });
    const id = pending.ownerRequest!.requestId;
    await expect(service.confirmOwner(task_id, id, { userId: "forged", isAdmin: false })).rejects.toMatchObject({ status: 403 });
    records.get(task_id)!.controlPlaneEnvironment = "prod";
    await expect(service.confirmOwner(task_id, id, actor)).rejects.toMatchObject({ status: 409 });
    records.get(task_id)!.controlPlaneEnvironment = "pre";
    const update = deps.tasks.update;
    deps.tasks.update = async () => { throw new Error("storage unavailable"); };
    await expect(service.confirmOwner(task_id, id, actor)).rejects.toThrow("storage unavailable");
    expect(agent.start).not.toHaveBeenCalled();
    deps.tasks.update = update;
    await service.confirmOwner(task_id, id, actor);
    expect(records.get(task_id)).toMatchObject({ status: "running", phase: "assess", ownerRequest: { status: "confirmed" } });
    expect(agent.start).not.toHaveBeenCalled();
    await flush(); expect(agent.start).toHaveBeenCalledOnce();
    await service.confirmOwner(task_id, id, actor); await flush();
    expect(agent.start).toHaveBeenCalledOnce();
  });

  it("maintenance resumes confirmed cooperation after a restart and expired execution without ending the request", async () => {
    const { service, records, agent } = setup();
    const { task_id } = await service.create(request, actor); await flush(); agent.start.mockClear();
    const task = records.get(task_id)!;
    task.ownerRequest = { requestId: "r1", revision: 1, status: "confirmed", reason: "permission", summary: "权限", instructions: "申请权限", evidence_ids: [], resumePhase: "assess", createdAtMs: 1, feedback: { userId: "owner", text: "", atMs: 2 } };
    task.requiresFreshObservation = true;
    task.execution = undefined;
    await service.maintenance();
    expect(agent.start).toHaveBeenCalledOnce();
    expect(records.get(task_id)?.status).toBe("running");
  });
  it("returns a post-plan Owner verification to verify and preserves the one-plan boundary", async () => {
    const { service, records } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    const task = records.get(task_id)!;
    task.evidence = [{ evidence_id: "read", tool_call_id: "call", source: "session_read", location: "original", excerpt: "权限已处理，原操作需核验" }];
    task.ownerRequest = { requestId: "r1", revision: 1, status: "confirmed", reason: "permission", summary: "权限", instructions: "申请权限", evidence_ids: ["read"], resumePhase: "verify", createdAtMs: 1 };
    task.plan = { summary: "原方案", basis: "b", scope: "s", verification: "v", actions: [], owner_cooperation: [], digest: "d", approvedAt: 1 };
    await expect(service.assess(task_id, task.version, { decision: "environment", summary: "再建一套方案", evidence_ids: ["read"] })).rejects.toMatchObject({ status: 409 });
    await service.assess(task_id, task.version, { decision: "observe", summary: "核验既有方案和权限后的效果", evidence_ids: ["read"] });
    expect(records.get(task_id)).toMatchObject({ phase: "verify", plan: { digest: "d", approvedAt: 1 } });
  });
  it("cannot accept a post-feedback recovery claim from pre-feedback evidence", async () => {
    const { service, records } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    const task = records.get(task_id)!;
    task.evidence = [{ evidence_id: "old", tool_call_id: "call", source: "session_read", location: "original", excerpt: "反馈前的现场" }];
    task.ownerRequest = { requestId: "r1", revision: 1, status: "confirmed", reason: "permission", summary: "权限", instructions: "申请权限", evidence_ids: ["old"], resumePhase: "assess", createdAtMs: 1 };
    task.requiresFreshObservation = true;
    await expect(service.assess(task_id, task.version, { decision: "observe", summary: "Owner说好了", evidence_ids: ["old"] })).rejects.toMatchObject({ status: 409 });
    task.phase = "observe";
    await expect(service.observe(task_id, task.version, { delivery: "unconfirmed", response: "none", effect: "recovered", summary: "Owner说好了", evidence_ids: ["old"] }, [])).rejects.toMatchObject({ status: 409 });
    task.execution = { executionId: "exec", stepId: "step", ticketDigest: "digest", jobId: "job", ccSessionId: null,
      state: "running", leaseExpiresAt: 1000, decisionDeadlineAt: null, lastHeartbeatAt: 1, invalidatedAt: null };
    await expect(service.finishForOwnerHandoff(task_id, task.version, { summary: "Owner说好了，已恢复仅剩建议", evidence_ids: ["old"], executionId: "exec", effect: "recovered" })).rejects.toMatchObject({ status: 409 });
    expect(records.get(task_id)?.status).toBe("running");
  });
  it("passes the frozen Bot and target environment to Owner notification policy without recording a disabled send", async () => {
    const { service, records, notifier } = setup();
    notifier.notify.mockResolvedValue(false);
    const ack = await service.create(request, actor); await flush();
    const task = records.get(ack.task_id)!;
    task.phase = "plan";
    task.level = "approval";
    await service.plan(task.taskId, task.version, { summary: "需要 Owner 开通权限", basis: "已核实权限受限", scope: "目标服务权限",
      verification: "权限恢复后核查原任务", actions: [], owner_cooperation: [{ request: "申请服务权限", basis: "服务明确拒绝访问" }] });
    expect(notifier.notify).toHaveBeenCalledExactlyOnceWith({ ownerId: "owner", botId: "default", targetEnvironment: "prod",
      taskId: task.taskId, sessionKey: task.request.session_key, kind: "plan", summary: "需要 Owner 开通权限" });
    const saved = records.get(task.taskId)!;
    expect(saved.status).toBe("waiting_owner");
    expect(saved.timeline.some(event => event.kind === "notification")).toBe(false);
  });

  it("counts a failed creation once without exposing diagnosis or credentials or replacing the error", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, deps } = setup();
    const error = Object.assign(new Error("target resolution failed; password=private-value"), { code: "TARGET_LOOKUP_FAILED" });
    deps.resolveTarget = async () => { throw error; };
    await expect(service.create({ ...request, diagnosis: "PRIVATE_DIAGNOSIS" }, actor)).rejects.toBe(error);
    expect(output.mock.calls[0][0]).toBe("session-recovery");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events).toEqual([expect.objectContaining({ job: "session_recovery_task", event: "CREATE_FAILED", outcome: "failed",
      count: 1, eventId: "evt", botId: "default", environment: "pre", targetEnvironment: "prod", errorCode: "TARGET_LOOKUP_FAILED" })]);
    expect(events[0].reason).toContain("target resolution failed");
    expect(JSON.stringify(events)).not.toMatch(/PRIVATE_DIAGNOSIS|private-value/);
  });

  it("does not count accepted duplicates, invalid inputs or expected conflicts as creation failures", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, deps } = setup();
    const first = await service.create(request, actor); await flush();
    expect(await service.create(request, actor)).toEqual(first);
    await expect(service.create({ ...request, diagnosis: "changed" }, actor)).rejects.toMatchObject({ status: 409 });
    await expect(service.create({}, actor)).rejects.toMatchObject({ status: 422 });
    await expect(service.create({ ...request, event_id: "forbidden" }, { userId: "other", isAdmin: false })).rejects.toMatchObject({ status: 403 });
    deps.resolveTarget = async () => { throw new SessionRecoveryError(422, "invalid_recovery_target", "binding mismatch"); };
    await expect(service.create({ ...request, event_id: "mismatch" }, actor)).rejects.toMatchObject({ status: 422 });
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "created")).toHaveLength(1);
    expect(events.filter(event => event.event === "CREATE_FAILED")).toEqual([]);
  });

  it("counts only exhausted technical execution and does not recount duplicate or ordinary blocked results", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    for (let attempt = 0; attempt < 3; attempt++) {
      await service.executionFailure(task_id, "AIS interrupted", undefined, true, undefined, "AIS_EXECUTION_FAILED");
    }
    await service.executionFailure(task_id, "late duplicate", undefined, true, undefined, "AIS_EXECUTION_FAILED");
    const blocked = await service.create({ ...request, event_id: "blocked" }, actor); await flush();
    await service.executionFailure(blocked.task_id, "业务受阻，需要现场证据");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "retry_scheduled")).toHaveLength(2);
    expect(events.filter(event => event.event === "EXECUTION_FAILED")).toEqual([expect.objectContaining({
      taskId: task_id, eventId: "evt", phase: "assess", attempt: 3, count: 1, environment: "pre", errorCode: "AIS_EXECUTION_FAILED" })]);
  });

  it("records terminal technical failure before releasing the Agent and only after persistence succeeds", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, agent, deps } = setup();
    const first = await service.create(request, actor); await flush();
    agent.release.mockRejectedValueOnce(new Error("release storage unavailable"));
    await expect(service.executionFailure(first.task_id, "startup failed", undefined, false, undefined, "AGENT_START_FAILED")).rejects.toThrow("release storage unavailable");
    await service.executionFailure(first.task_id, "late result", undefined, false, undefined, "AGENT_START_FAILED");
    const second = await service.create({ ...request, event_id: "persist-failure" }, actor); await flush();
    vi.spyOn(deps.tasks, "update").mockRejectedValueOnce(new Error("state write unavailable"));
    await expect(service.executionFailure(second.task_id, "startup failed", undefined, false, undefined, "AGENT_START_FAILED")).rejects.toThrow("state write unavailable");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "EXECUTION_FAILED").map(event => event.taskId)).toEqual([first.task_id]);
  });

  it("does not turn verification failure, Owner rejection, cancellation or debug execution into a technical alarm", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, records } = setup();
    const verify = await service.create(request, actor); await flush();
    const task = records.get(verify.task_id)!;
    task.phase = "verify"; task.evidence = [{ evidence_id: "read", tool_call_id: "call", source: "session_read", location: "original", excerpt: "not yet recovered" }];
    await service.observe(task.taskId, task.version, { delivery: "unconfirmed", response: "unknown", effect: "not_recovered", summary: "仍未恢复", evidence_ids: ["read"] }, []);
    const reject = await service.create({ ...request, event_id: "rejected" }, actor); await flush();
    const pending = records.get(reject.task_id)!; pending.status = "waiting_owner";
    pending.plan = { digest: "plan", approvedAt: null, summary: "", basis: "", scope: "", verification: "", actions: [], owner_cooperation: [] };
    await service.decide(pending.taskId, "plan", false, actor);
    const cancel = await service.create({ ...request, event_id: "cancel" }, actor); await flush();
    await service.terminate(cancel.task_id, actor);
    const debug = await service.create({ ...request, event_id: "debug", execution_level: "debug" }, actor); await flush();
    await service.executionFailure(debug.task_id, "debug execution interrupted", undefined, false, undefined, "AIS_EXECUTION_FAILED");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "EXECUTION_FAILED")).toEqual([]);
    expect(events.filter(event => event.event === "state_changed").map(event => event.status)).toContain("canceled");
  });

  it("retains the cause of a maintenance scan failure without emitting task failure counters", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, deps } = setup();
    vi.spyOn(deps.tasks, "listForMaintenance").mockRejectedValueOnce(new Error("scan DB unavailable"));
    await expect(service.maintenance()).rejects.toThrow("scan DB unavailable");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events).toEqual([expect.objectContaining({ job: "session_recovery_maintenance", event: "scan_failed", reason: "scan DB unavailable" })]);
  });

  it("does not count an optimistic write conflict before a later successful terminal settlement", async () => {
    const output = vi.mocked(logRepairEvent).mockClear();
    const { service, deps, records } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    vi.spyOn(deps.tasks, "update").mockImplementationOnce(async (id, _version, change) => {
      change(structuredClone(records.get(id)!));
      return null;
    });
    await service.executionFailure(task_id, "AIS failed", undefined, false, undefined, "AIS_EXECUTION_FAILED");
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "EXECUTION_FAILED")).toHaveLength(1);
    expect(events.filter(event => event.event === "state_changed")).toHaveLength(1);
  });
  it("retains the upstream physical Session ID in storage and task detail", async () => {
    const { service, records } = setup();
    const { task_id } = await service.create({ ...request, session_id: "original" }, actor);
    expect(records.get(task_id)!.request.session_id).toBe("original");
    expect((await service.detail(task_id, actor)).request.session_id).toBe("original");
  });
  it("starts an independent Agent without backend advice once for an idempotent trigger", async () => {
    const { service, agent, delivery } = setup();
    const accepted = await service.create(request, actor);
    await flush();
    expect(agent.start).toHaveBeenCalledTimes(1);
    expect(delivery.create).not.toHaveBeenCalled();
    expect(await service.create(request, actor)).toEqual(accepted);
    await flush(); expect(agent.start).toHaveBeenCalledTimes(1);
  });
  it("rejects changed content under the same event and delivery key", async () => {
    const { service } = setup(); await service.create(request, actor);
    await expect(service.create({ ...request, diagnosis: "另一故障" }, actor)).rejects.toMatchObject({ status: 409 });
  });
  it.each([null, "TC.SKILL.EXECUTION", "TC.MODEL.REASONING", "TC.NEW.CATEGORY"])("routes %s straight to Agent assessment without a generic message", async tc_fault_label => {
    const { service, agent, delivery } = setup();
    const ack = await service.create({ ...request, tc_fault_label }, actor); await flush();
    expect(agent.start).toHaveBeenCalledTimes(1); expect(delivery.create).not.toHaveBeenCalled();
    expect((await service.detail(ack.task_id, actor)).phase).toBe("assess");
    await service.maintenance(); expect(delivery.create).not.toHaveBeenCalled();
  });
  it.each(["direct", "agent"])("rejects removed intervention mode %s", async mode => {
    const { service } = setup();
    await expect(service.create({ ...request, initial_intervention_mode: mode }, actor)).rejects.toMatchObject({ status: 422 });
  });
  it("runs read-only Debug analysis without sending a real message", async () => {
    const { service, agent, delivery, notifier } = setup();
    const ack = await service.create({ ...request, execution_level: "debug" }, actor); await flush();
    expect(agent.start).toHaveBeenCalledTimes(1);
    expect(delivery.create).not.toHaveBeenCalled(); expect(notifier.notify).not.toHaveBeenCalled();
    const task = await service.detail(ack.task_id, actor);
    expect(task.phase).toBe("assess"); expect(task.timeline[1].summary).toContain("不实际发送");
  });
  it("retains the exact saved result when callback fails and never resends advice", async () => {
    const { service, records, delivery, reporter } = setup(true);
    const ack = await service.create(request, actor); await flush();
    await service.recordDelivery(ack.task_id, { event_id: "evt", delivery: { delivery_key: "send1", status: "accepted",
      result_text: "通道接受", started_at_ms: 1, finished_at_ms: 2, error: null } });
    await service.reportResults();
    expect(reporter.report).not.toHaveBeenCalled();
    await service.executionFailure(ack.task_id, "执行失败，任务结束");
    await service.reportResults();
    const saved = records.get(ack.task_id)!.pending_results[0];
    expect(saved.result_version).toBe(3);
    reporter.report.mockResolvedValueOnce(undefined);
    await service.create(request, actor);
    await service.reportResults();
    expect(reporter.report).toHaveBeenLastCalledWith(saved);
    expect(records.get(ack.task_id)!.pending_results).toEqual([]);
    expect(delivery.create).not.toHaveBeenCalled();
  });
  it("does not reveal another Owner's task", async () => {
    const { service } = setup(); const ack = await service.create(request, actor);
    await expect(service.detail(ack.task_id, { userId: "stranger", isAdmin: false })).rejects.toMatchObject({ status: 403 });
  });
  it("lets a current Bot collaborator read a private task without granting operations or public sharing", async () => {
    const { service, deps, records } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    const canReadAsBotCollaborator = vi.fn().mockResolvedValue(true);
    const reader = new SessionRecoveryTaskService({ ...deps, canReadAsBotCollaborator });
    const collaborator = { userId: "member", isAdmin: false };
    expect(await reader.detail(task_id, collaborator)).toMatchObject({
      shared: false, canOperate: false, canManageShare: false,
    });
    expect(canReadAsBotCollaborator).toHaveBeenCalledExactlyOnceWith("member", request.target);
    await expect(reader.setTaskShared(task_id, true, collaborator)).rejects.toMatchObject({ status: 403 });
    await expect(reader.terminate(task_id, collaborator)).rejects.toMatchObject({ status: 403 });
    expect(records.get(task_id)?.shared).toBe(false);
    canReadAsBotCollaborator.mockResolvedValue(false);
    await expect(reader.detail(task_id, collaborator)).rejects.toMatchObject({ status: 403 });
  });
  it("lists private linked tasks for administrators without exposing them to other users", async () => {
    const { service } = setup(); const { task_id } = await service.create(request, actor);
    expect(await service.list(request.event_id, { userId: "admin", isAdmin: true })).toMatchObject([{ task_id }]);
    expect(await service.list(request.event_id, { userId: "reader", isAdmin: false })).toEqual([]);
    expect(await service.list("other-event", { userId: "admin", isAdmin: true })).toEqual([]);
  });
  it("keeps legacy tasks private and sharing idempotent without changing the workflow or reporting results", async () => {
    const { service, records, agent, reporter } = setup();
    const { task_id } = await service.create(request, actor); await flush();
    delete records.get(task_id)!.shared;
    expect(await service.detail(task_id, actor)).toMatchObject({ shared: false, canOperate: true, canManageShare: true });
    await expect(service.detail(task_id, { userId: "reader", isAdmin: false })).rejects.toMatchObject({ status: 403 });
    await expect(service.setTaskShared(task_id, "true" as any, actor)).rejects.toMatchObject({ status: 422 });
    await service.setTaskShared(task_id, true, actor);
    const version = records.get(task_id)!.version;
    await service.setTaskShared(task_id, true, actor);
    expect(records.get(task_id)!.version).toBe(version);
    expect(await service.detail(task_id, { userId: "reader", isAdmin: false })).toMatchObject({ shared: true, canOperate: false, canManageShare: false });
    expect(await service.list(request.event_id, { userId: "reader", isAdmin: false })).toHaveLength(1);
    await service.setTaskShared(task_id, false, { userId: "admin", isAdmin: true });
    await expect(service.detail(task_id, { userId: "reader", isAdmin: false })).rejects.toMatchObject({ status: 403 });
    expect(records.get(task_id)).toMatchObject({ phase: "assess", status: "running", pending_results: [] });
    expect(agent.start).toHaveBeenCalledTimes(1); expect(reporter.report).not.toHaveBeenCalled();
  });
});

it("slow callbacks cannot block state changes or expire an Owner wait", async () => {
  const { service, records, reporter, agent } = setup();
  const ack = await service.create(request, actor); await flush();
  await service.executionFailure(ack.task_id, "执行失败，任务结束");
  const waiting = await service.create({ ...request, event_id: "owner-wait" }, actor); await flush();
  const value = records.get(waiting.task_id)!;
  value.status = "waiting_owner"; value.ownerDeadlineAt = 1;
  value.execution = { state: "running" } as any;
  agent.release.mockClear();
  let unblock!: () => void;
  reporter.report.mockImplementation(() => new Promise<void>(resolve => { unblock = resolve; }));
  const reporting = service.reportResults(); await flush();
  await service.maintenance(); expect(agent.release).not.toHaveBeenCalled();
  expect(records.get(ack.task_id)!.pending_results).toHaveLength(1);
  unblock(); await reporting;
  expect(records.get(ack.task_id)!.pending_results).toEqual([]);
});


it("terminal annotations and repeated failure reports do not enqueue another result", async () => {
  const { service, records, reporter } = setup();
  const ack = await service.create(request, actor); await flush();
  await service.executionFailure(ack.task_id, "执行失败");
  await service.reportResults();
  await service.annotate(ack.task_id, actor, { layer: "effect", correct: null, reason: "人工复核", evidence_ids: [] });
  await service.executionFailure(ack.task_id, "重复上报");
  await service.reportResults();
  expect(reporter.report).toHaveBeenCalledOnce();
  expect(records.get(ack.task_id)!.pending_results).toEqual([]);
});
it("makes concurrent stop requests idempotent without a duplicate final result", async () => {
  const { service, records } = setup();
  const ack = await service.create(request, actor); await flush();
  const responses = await Promise.all([service.terminate(ack.task_id, actor), service.terminate(ack.task_id, actor)]);
  expect(responses.every(value => value.status === "canceled")).toBe(true);
  const saved = records.get(ack.task_id)!;
  expect(saved.timeline.filter(event => event.kind === "termination")).toHaveLength(1);
  expect(saved.pending_results).toHaveLength(1);
  expect(saved.pending_results[0].status).toBe("canceled");
});

it("validates runtime identity only through the recovery creation contract", () => {
  const runtime_identity = { stage: "online", deviceUuid: "DEVICE-" + "a".repeat(32), version: "V16" };
  expect(parseRequest({ ...request, target: { ...request.target, runtime_identity } }).target.runtime_identity).toEqual(runtime_identity);
  for (const patch of [{ stage: "unknown" }, { deviceUuid: "BOT-wrong" }, { version: "16" }, { STAGE: "verify" }]) {
    expect(() => parseRequest({ ...request, target: { ...request.target, runtime_identity: { ...runtime_identity, ...patch } } })).toThrow();
  }
  const completeIdentity = { TOKEN: "dummy", CLIENT_ID: runtime_identity.deviceUuid,
    OWNER_ID: "owner", BOT_ID: "default", ENTITY_ID: "owner", ENTITY_TYPE: "staff", BOT_TYPE: "service",
    AGENT_CODE: "example-service", ADMINS: "", STAGE: "online", VERSION: "V16", future_metadata: { supported: true } };
  expect(parseRequest({ ...request, target: { ...request.target, runtime_identity: completeIdentity } }).target.runtime_identity).toEqual(runtime_identity);
  expect(parseRequest({ ...request, target: { ...request.target, runtime_identity: { ...completeIdentity, ...runtime_identity } } }).target.runtime_identity).toEqual(runtime_identity);
  expect(parseRequest({ ...request, target: { ...request.target, runtime_identity: { ...completeIdentity, STAGE: "draft" } } }).target.runtime_identity)
    .toEqual({ stage: "draft", deviceUuid: runtime_identity.deviceUuid });
});

it("creates and starts a personal Bot task using its normal binding without service locators", async () => {
  const { deps, agent } = setup();
  const target = { environment: "prod" as const, ownerId: "owner", botId: "default", provider: "baas", bindingId: "12",
    deviceId: "BOT-personal", activeEngine: "openclaw", botType: "personal" };
  const resolveTarget = vi.fn().mockResolvedValue(recoveryTargetSnapshot(target));
  const service = new SessionRecoveryTaskService({ ...deps, resolveTarget });
  const input = { ...request, target: { ...request.target, runtime_identity: { BOT_TYPE: "personal", STAGE: "",
    TOKEN: "dummy", CLIENT_ID: "DEVICE-" + "a".repeat(32), VERSION: "V1" } } };
  const ack = await service.create(input, actor); await flush();
  expect(resolveTarget).toHaveBeenCalledWith(request);
  const task = await service.detail(ack.task_id, actor);
  expect(task.request.target).toEqual(request.target);
  expect(task.runtimeTarget?.target).toMatchObject(target);
  expect(task.runtimeTarget?.target).not.toHaveProperty("runtimeIdentity");
  expect(task.status).toBe("running");
  expect(agent.start).toHaveBeenCalledOnce();
  expect(await service.create(input, actor)).toEqual(ack);
  expect(resolveTarget).toHaveBeenCalledOnce();
});

it("persists the service instance identity and fences later requests from switching devices", async () => {
  const { deps } = setup();
  const runtimeIdentity = { stage: "online" as const, version: "V16", deviceUuid: "DEVICE-" + "a".repeat(32) };
  const target = { environment: "prod" as const, ownerId: "owner", botId: "default", provider: "baas", bindingId: "20",
    deviceId: "BOT-online", deviceUuid: runtimeIdentity.deviceUuid, providerDeviceId: "ARCA-SANDBOX-original@0", activeEngine: "openclaw", botType: "service", runtimeIdentity };
  const resolveTarget = vi.fn().mockResolvedValue(recoveryTargetSnapshot(target));
  const service = new SessionRecoveryTaskService({ ...deps, resolveTarget });
  const input = { ...request, target: { ...request.target, runtime_identity: runtimeIdentity } };
  const ack = await service.create(input, actor); await flush();
  const task = await service.detail(ack.task_id, actor);
  expect(task.request.target.runtime_identity).toEqual(runtimeIdentity);
  expect(task.runtimeTarget?.target).toMatchObject({ botType: "service", deviceUuid: runtimeIdentity.deviceUuid, runtimeIdentity });
  expect(await service.create(input, actor)).toEqual(ack);
  expect(resolveTarget).toHaveBeenCalledOnce();
  await expect(service.create({ ...input, target: { ...input.target, runtime_identity: { ...runtimeIdentity, deviceUuid: "DEVICE-" + "b".repeat(32) } } }, actor)).rejects.toMatchObject({ status: 409 });
  expect(recoveryTargetSnapshot({ ...target, deviceUuid: "DEVICE-" + "b".repeat(32) }).fingerprint).not.toBe(task.runtimeTarget!.fingerprint);
});

describe("task final summary", () => {
  it.each(["recovered", "not_recovered"] as const)("saves the overall summary with a terminal %s judgment and exposes it in detail", async effect => {
    const { service, records } = setup();
    const { task_id } = await service.create(request, actor);
    const value = records.get(task_id)!;
    value.phase = "verify";
    value.evidence = [{ evidence_id: "proof", tool_call_id: "read", source: "session_read", location: "line:1", excerpt: "原任务执行结果" }];
    const finalSummary = "用户要读取文档并总结。核实后发现调用格式错误，已通过恢复消息纠正调用；原任务的实际进展见结果。";
    await service.observe(task_id, value.version, { delivery: "confirmed", response: "acted", effect, summary: "完整的效果判断与证据", evidence_ids: ["proof"] }, [], finalSummary);
    expect((await service.detail(task_id, actor)).final_summary).toBe(finalSummary);
    expect(records.get(task_id)!.judgment?.summary).toBe("完整的效果判断与证据");
  });
  it("does not publish a first-stage summary as the final task summary", async () => {
    const { service, records } = setup();
    const { task_id } = await service.create(request, actor);
    const value = records.get(task_id)!; value.phase = "observe";
    value.evidence = [{ evidence_id: "proof", tool_call_id: "read", source: "session_read", location: "line:1", excerpt: "原任务仍受阻" }];
    await service.observe(task_id, value.version, { delivery: "unconfirmed", response: "none", effect: "not_recovered", summary: "需要完整方案", evidence_ids: ["proof"] }, [], "尚未执行的初步判断");
    expect(records.get(task_id)!.phase).toBe("plan");
    expect(records.get(task_id)!.final_summary).toBeUndefined();
  });
  it.each([undefined, "", 42, "x".repeat(2001), ["Authorization:", "Bearer", "unit-test"].join(" ")])("keeps the main result when optional summary is invalid: %s", async finalSummary => {
    const { service, records } = setup();
    const { task_id } = await service.create(request, actor);
    const value = records.get(task_id)!; value.phase = "verify";
    value.evidence = [{ evidence_id: "proof", tool_call_id: "read", source: "session_read", location: "line:1", excerpt: "结果" }];
    await service.observe(task_id, value.version, { delivery: "unconfirmed", response: "unknown", effect: "recovered", summary: "原任务已继续", evidence_ids: ["proof"] }, [], finalSummary);
    expect(records.get(task_id)!).toMatchObject({ status: "completed", judgment: { effect: "recovered" } });
    expect(records.get(task_id)!.final_summary).toBeUndefined();
  });
});

it("clears a paused summary when the same task resumes observation", async () => {
  const { service, records } = setup();
  const { task_id } = await service.create(request, actor);
  const value = records.get(task_id)!; value.phase = "observe"; value.observationDeadlineAt = 99;
  await service.observe(task_id, value.version, { delivery: "unconfirmed", response: "unknown", effect: "unknown", summary: "等待新的执行证据", evidence_ids: [] }, [], "已发送指引，观察到期仍无法核实原任务恢复，等待后续证据。");
  expect(records.get(task_id)!).toMatchObject({ status: "waiting_effect", final_summary: expect.stringContaining("等待后续证据") });
  await service.resume(task_id);
  expect(records.get(task_id)!).toMatchObject({ status: "running", requiresFreshObservation: true });
  expect(records.get(task_id)!.final_summary).toBeUndefined();
});


it.each(["observe", "verify"] as const)("preserves recovery at maintenance expiry after waiting for delivery in %s", async phase => {
  let now = 100;
  const { service, records, agent } = setup(false, () => now);
  const { task_id } = await service.create(request, actor); await flush();
  await service.recordDelivery(task_id, { event_id: "evt", delivery: { delivery_key: "send1", status: "accepted",
    result_text: "accepted", started_at_ms: 1, finished_at_ms: 2, error: null } });
  const task = records.get(task_id)!; task.phase = phase;
  task.evidence = [{ evidence_id: "proof", tool_call_id: "read", source: "runtime_inspect", location: "files", excerpt: "原失败步骤已成功" }];
  const deadline = task.observationDeadlineAt!;
  await service.observe(task_id, task.version, { delivery: "unconfirmed", response: "unknown", effect: "recovered",
    summary: "原任务已恢复，等待触达证据", evidence_ids: ["proof"] }, [], "不应提前保存的最终总结");
  expect(records.get(task_id)).toMatchObject({ status: "running", phase, pending_results: [], observationDeadlineAt: deadline, judgment: { effect: "recovered" } });
  expect(records.get(task_id)?.final_summary).toBeUndefined();
  expect(agent.release).not.toHaveBeenCalled();
  now = deadline;
  await service.maintenance();
  expect(records.get(task_id)).toMatchObject({ status: "completed", phase, observationDeadlineAt: deadline,
    judgment: { effect: "recovered", delivery: "unconfirmed", evidence_ids: ["proof"] },
    pending_results: [expect.objectContaining({ effect: "recovered", intervention_status: "unknown" })] });
  expect(agent.release).toHaveBeenCalledOnce();
});


it("retains confirmed business recovery when an execution ends before delivery is confirmed", async () => {
  const { service, records } = setup();
  const { task_id } = await service.create(request, actor); await flush();
  const task = records.get(task_id)!; task.phase = "observe";
  task.judgment = { delivery: "unconfirmed", response: "unknown", effect: "recovered", summary: "业务产物已恢复，等待触达证据", evidence_ids: [] };
  await service.executionFailure(task_id, "AIS execution timed out");
  expect(records.get(task_id)).toMatchObject({ status: "failed", judgment: { effect: "recovered", delivery: "unconfirmed" },
    pending_results: [expect.objectContaining({ status: "failed", effect: "recovered", intervention_status: "unknown" })] });
});
