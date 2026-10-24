import Database from "better-sqlite3";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import type { RecoveryStoredTask } from "../task-contracts.js";

let db: SqliteDatabase, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService;
let now: number, objects: Map<string, Buffer>;
let artifacts: ConstructorParameters<typeof SessionRecoveryTaskRepository>[2];
const request = { event_id: "fixture", tc_fault_label: "TC.MCP.CALL", diagnosis: "工具调用失败", delivery_key: "original-request",
  session_key: "agent:main:original", target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" } };
const current = async (id: string) => (await tasks.find(id))!;
async function create(event = "fixture") {
  const ack = await service.create({ ...request, event_id: event }, { userId: "owner", isAdmin: false });
  await vi.waitFor(async () => expect((await current(ack.task_id)).execution?.jobId).toBeTruthy());
  return current(ack.task_id);
}
async function update(task: RecoveryStoredTask, change: Partial<RecoveryStoredTask>) {
  return (await tasks.update(task.taskId, task.version, value => ({ ...value, ...change, version: value.version + 1 })))!;
}
beforeEach(async () => {
  now = Date.now(); objects = new Map();
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  const repo = new EvolveRepository(db);
  artifacts = { putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: vi.fn(async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" })) };
  tasks = new SessionRecoveryTaskRepository(db, repo, artifacts);
  const target = { provider: "arca" as const, environment: "prod" as const, ownerId: "owner", botId: "default", bindingId: "binding", deviceId: "device", activeEngine: "openclaw" as const };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"),
    ais: { execute: vi.fn(async () => "job"), getJobStatusDetail: vi.fn(async () => ({ status: "stopped" as const, rawStatus: "stopped", errorMessage: null })),
      stopExecution: vi.fn(), getJobStatus: vi.fn() },
    runtime: { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() }, targets: { resolve: async () => target },
    business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1, nasRoots: [],
    leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now });
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier: { notify: vi.fn() }, reporter: { report: vi.fn() },
    observationMs: 3600000, now: () => now, resolveTarget: async () => recoveryTargetSnapshot(target) });
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); });

it("counts concurrent failure reports for one execution once using the real task store", async () => {
  const task = await create(), originalRelease = agent.release.bind(agent);
  let arrivals = 0, unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  vi.spyOn(agent, "release").mockImplementation(async input => {
    if (++arrivals === 2) unblock();
    await gate; await originalRelease(input);
  });
  await Promise.all([service.executionFailure(task.taskId, "AIS_RESULT_MISSING", task.execution!.executionId, true),
    service.executionFailure(task.taskId, "AIS_RESULT_MISSING", task.execution!.executionId, true)]);
  const saved = await current(task.taskId);
  expect(saved.executionRetry).toMatchObject({ failures: 1, retryAt: now + 20000 });
  expect(saved.execution!.state).toBe("ended");
  expect(saved.timeline.filter(event => event.payload.retryable === true)).toHaveLength(1);
});

it("does not load object artifacts for healthy, waiting or other-control-plane tasks", async () => {
  await create("healthy");
  await update(await create("owner"), { status: "waiting_owner" });
  await update(await create("other-plane"), { controlPlaneEnvironment: "prod" });
  vi.mocked(artifacts.getObject).mockClear();
  expect(await tasks.listForMaintenance(200, "", "pre", now)).toEqual({ tasks: [], nextTaskId: "" });
  expect(artifacts.getObject).not.toHaveBeenCalled();
});

it("advances an empty filtered page and finds the later due task", async () => {
  const all = [await create("one"), await create("two")].sort((a, b) => a.taskId.localeCompare(b.taskId));
  const due = await update(all[1], { execution: { ...all[1].execution!, state: "ended" } });
  vi.mocked(artifacts.getObject).mockClear();
  const first = await tasks.listForMaintenance(1, "", "pre", now);
  expect(first).toEqual({ tasks: [], nextTaskId: all[0].taskId });
  expect(artifacts.getObject).not.toHaveBeenCalled();
  const second = await tasks.listForMaintenance(1, first.nextTaskId, "pre", now);
  expect(second.tasks.map(task => task.taskId)).toEqual([due.taskId]);
});

it("uses the maintenance clock to select observation expiry and only due retries", async () => {
  let task = await create();
  task = await update(task, { phase: "observe", observationDeadlineAt: now + 1000 });
  expect((await tasks.listForMaintenance(200, "", "pre", now)).tasks).toEqual([]);
  expect((await tasks.listForMaintenance(200, "", "pre", now + 1000)).tasks.map(value => value.taskId)).toEqual([task.taskId]);
  task = await update(task, { observationDeadlineAt: null, execution: { ...task.execution!, state: "ended" },
    executionRetry: { failures: 1, executionId: task.execution!.executionId, retryAt: now + 20000 } });
  expect((await tasks.listForMaintenance(200, "", "pre", now + 19999)).tasks).toEqual([]);
  expect((await tasks.listForMaintenance(200, "", "pre", now + 20000)).tasks.map(value => value.taskId)).toEqual([task.taskId]);
});
