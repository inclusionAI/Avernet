import { recoveryTargetSnapshot } from "../agent-service.js";
import { describe, expect, it, vi } from "vitest";
import type { RecoveryStoredTask, RecoveryTaskStore } from "../task-contracts.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { parseRequest } from "../validation.js";
const request = { event_id: "evt", tc_fault_label: "TC.MCP.CALL", delivery_key: "send1", diagnosis: "操作失败",
  session_key: "agent:main:session:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } };
const actor = { userId: "owner", isAdmin: false };
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
function setup(callbackFails = false) {
  const records = new Map<string, RecoveryStoredTask>();
  const store: RecoveryTaskStore = {
    create: async value => { if (records.has(value.taskId)) throw new Error("duplicate"); records.set(value.taskId, structuredClone(value)); },
    find: async id => records.has(id) ? structuredClone(records.get(id)!) : null,
    listForEvent: async (eventId, ownerId, isAdmin) => [...records.values()].filter(value => value.request.event_id === eventId && (isAdmin || value.request.target.entity_id === ownerId || value.shared === true)),
    listForReporting: async (limit, afterTaskId = "") => [...records.values()].filter(value => value.taskId > afterTaskId && value.pending_results.length > 0).sort((a, b) => a.taskId.localeCompare(b.taskId)).slice(0, limit),
    listForMaintenance: async (limit, afterTaskId = "") => [...records.values()].filter(value => value.taskId > afterTaskId).slice(0, limit),
    update: async (id, version, change) => {
      const value = records.get(id); if (!value || value.version !== version) return null;
      const changed = change(structuredClone(value)); records.set(id, structuredClone(changed)); return changed;
    },
    acknowledgeResult: async (id, version) => { const value = records.get(id)!; value.pending_results = value.pending_results.filter(item => item.result_version !== version); },
    appendNotification: async (id, event) => { const value = records.get(id)!; if (value.timeline.some(item => item.event_id === event.event_id)) return false; value.timeline.push(event); return true; },
  };
  const delivery = { create: vi.fn().mockResolvedValue({ event_id: "evt", delivery_key: "send1", status: "accepted" }) };
  const agent = { start: vi.fn().mockResolvedValue(undefined), release: vi.fn().mockResolvedValue(undefined), listExecutions: vi.fn().mockResolvedValue([]),
    terminate: vi.fn().mockResolvedValue({ status: "remote_stopped", aisJobId: "job" }) };
  const notifier = { notify: vi.fn().mockResolvedValue(undefined) };
  const reporter = { report: callbackFails ? vi.fn().mockRejectedValue(new Error("callback offline")) : vi.fn().mockResolvedValue(undefined) };
  const deps = { controlPlaneEnvironment: "pre" as const, tasks: store, agent, notifier, reporter,
    observationMs: 3600000, now: () => 100 };
  return { records, delivery, agent, notifier, reporter, deps, service: new SessionRecoveryTaskService(deps) };
}

describe("self-healing task orchestration", () => {
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
