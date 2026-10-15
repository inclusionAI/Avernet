import { describe, expect, it, vi } from "vitest";
import type { RecoveryStoredTask, RecoveryTaskStore } from "../task-contracts.js";
import { SessionRecoveryTaskService } from "../task-service.js";
const request = { event_id: "evt", tc_fault_label: "TC.MCP.CALL", delivery_key: "send1", diagnosis: "操作失败",
  session_key: "agent:main:session:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } };
const actor = { userId: "owner", isAdmin: false };
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
function setup(callbackFails = false) {
  const records = new Map<string, RecoveryStoredTask>();
  const store: RecoveryTaskStore = {
    create: async value => { if (records.has(value.taskId)) throw new Error("duplicate"); records.set(value.taskId, structuredClone(value)); },
    find: async id => records.has(id) ? structuredClone(records.get(id)!) : null,
    listForEvent: async (eventId, ownerId) => [...records.values()].filter(value => value.request.event_id === eventId && value.request.target.entity_id === ownerId),
    listForReporting: async (limit, afterTaskId = "") => [...records.values()].filter(value => value.taskId > afterTaskId && value.pending_results.length > 0).sort((a, b) => a.taskId.localeCompare(b.taskId)).slice(0, limit),
    listForMaintenance: async (limit, afterTaskId = "") => [...records.values()].filter(value => value.taskId > afterTaskId).slice(0, limit),
    update: async (id, version, change) => {
      const value = records.get(id); if (!value || value.version !== version) return null;
      const changed = change(structuredClone(value)); records.set(id, structuredClone(changed)); return changed;
    },
    acknowledgeResult: async (id, version) => { const value = records.get(id)!; value.pending_results = value.pending_results.filter(item => item.result_version !== version); },
  };
  const delivery = { create: vi.fn().mockResolvedValue({ event_id: "evt", delivery_key: "send1", status: "accepted" }) };
  const agent = { start: vi.fn().mockResolvedValue(undefined), release: vi.fn().mockResolvedValue(undefined) };
  const notifier = { notify: vi.fn().mockResolvedValue(undefined) };
  const reporter = { report: callbackFails ? vi.fn().mockRejectedValue(new Error("callback offline")) : vi.fn().mockResolvedValue(undefined) };
  const deps = { tasks: store, agent, notifier, reporter,
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
