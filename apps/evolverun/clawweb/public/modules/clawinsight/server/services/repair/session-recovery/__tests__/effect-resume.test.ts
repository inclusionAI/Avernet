import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { AgentExecutionService, agentExecutionContract, createAgentExecutionStep } from "../../common/agent-execution.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryAgentService } from "../agent-service.js";
import type { RecoveryStoredTask } from "../task-contracts.js";

const hour = 3600000;
const unknown = { delivery: "confirmed", response: "acted", effect: "unknown", summary: "尚无业务结果", evidence_ids: [] } as const;
let db: SqliteDatabase, tasks: SessionRecoveryTaskRepository, repo: EvolveRepository, now: number;
let service: SessionRecoveryTaskService, deps: ConstructorParameters<typeof SessionRecoveryTaskService>[0];
let start: ReturnType<typeof vi.fn<(task: RecoveryStoredTask) => Promise<void>>>, getObject: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  const objects = new Map<string, Buffer>();
  getObject = vi.fn(async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }));
  tasks = new SessionRecoveryTaskRepository(db, repo, {
    getObject,
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
  });
  now = 30 * hour;
  start = vi.fn(async (_task: RecoveryStoredTask) => {});
  deps = { tasks, controlPlaneEnvironment: "pre", observationMs: hour, now: () => now,
    agent: { start, release: async () => {}, terminate: async () => ({ status: "remote_stopped", aisJobId: null }), listExecutions: async () => [] },
    notifier: { notify: async () => false }, reporter: { report: async () => {} } };
  service = new SessionRecoveryTaskService(deps);
});
afterEach(async () => { await db.close(); });

async function enable(concurrency = 2) {
  await new AppConfigRepository(db).create({ config_key: "session_recovery_effect_resume_pre", config_json: JSON.stringify({ concurrency }) });
}
async function seed(id: string, patch: Partial<RecoveryStoredTask> = {}) {
  const task: RecoveryStoredTask = {
    schemaVersion: "session-recovery-task/v1", taskId: id, controlPlaneEnvironment: "pre",
    level: "automatic", status: "waiting_effect", phase: "observe", version: 1,
    request: { event_id: id, delivery_key: id, tc_fault_label: null, diagnosis: "fixture", session_key: "original",
      target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "TE" } },
    observationDeadlineAt: now - hour, ownerDeadlineAt: null, plan: null,
    judgment: { ...unknown, evidence_ids: [] }, evidence: [], pending_results: [],
    timeline: [], effectWait: { firstAt: now - hour, sinceAt: now - hour, resumes: 0 }, ...patch,
  };
  await tasks.create(task, `${id}-1`);
  await tasks.update(id, 1, value => ({ ...value, version: 2 }));
  return task;
}
const current = async (id: string) => (await tasks.find(id))!;

it("waits a full hour and uses the same task, phase, and original session", async () => {
  await enable();
  const prior = await seed("wait", { phase: "verify", effectWait: { firstAt: now - hour + 1, sinceAt: now - hour + 1, resumes: 0 } });
  await service.maintenance();
  expect(start).not.toHaveBeenCalled();
  now++;
  await service.maintenance();
  const resumed = await current("wait");
  expect(resumed).toMatchObject({ taskId: prior.taskId, request: prior.request, status: "running", phase: "verify",
    requiresFreshObservation: true, effectWait: { firstAt: prior.effectWait!.firstAt, resumes: 1 } });
  await vi.waitFor(() => expect(start).toHaveBeenCalledOnce());
});

it("keeps new starts on later pages ahead of resumption and avoids hydrating the waiting backlog", async () => {
  await enable();
  await seed("000-due");
  for (let index = 0; index < 205; index++) {
    await seed(`backlog-${String(index).padStart(3, "0")}`,
      { effectWait: { firstAt: now - 10, sinceAt: now - 10, resumes: 0 } });
  }
  await seed("zzz-new", { status: "running", phase: "assess", effectWait: undefined });
  getObject.mockClear();
  const query = vi.spyOn(db, "query");
  await service.maintenance();
  await vi.waitFor(() => expect(start.mock.calls.map(([task]) => task.taskId)).toEqual(["zzz-new", "000-due"]));
  expect(getObject.mock.calls.length).toBeLessThan(25);
  expect(query.mock.calls.filter(([sql]) => sql.includes("ORDER BY task_id ASC LIMIT"))).toHaveLength(3);
});

it("shares two slots across maintenance workers and holds them until the AIS step is terminal", async () => {
  await enable();
  await seed("a"); await seed("b"); await seed("c");
  const other = new SessionRecoveryTaskService(deps);
  // Both workers must use the same persisted configuration, even when their scans overlap.
  await Promise.all([service.maintenance(), other.maintenance()]);
  await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  expect((await current("c")).status).toBe("waiting_effect");
  const b = await current("b");
  const executor = new AgentExecutionService({ nowSeconds: () => Math.floor(now / 1000), leaseSeconds: 120 });
  await tasks.update("b", b.version, value => ({ ...value, version: value.version + 1,
    execution: executor.issue("exec-b", "b-AIS-1").execution, executionNo: 1 }));

  await repo.createStep({ stepId: "a-AIS-1", taskId: "a", stepType: "agent_execution", stepNo: -1, command: "session_recovery" });
  await db.exec("UPDATE ce_steps SET status = 'running', bot_run_id = 'job-a' WHERE step_id = ?", ["a-AIS-1"]);
  const active = await current("a");
  await tasks.update("a", active.version, value => ({ ...value, version: value.version + 1, executionNo: 1, requiresFreshObservation: false }));
  await service.observe("a", (await current("a")).version, { ...unknown, evidence_ids: [] }, []);
  await service.maintenance();
  expect(start).toHaveBeenCalledTimes(2);
  await db.exec("UPDATE ce_steps SET status = 'succeeded' WHERE step_id = ?", ["a-AIS-1"]);
  await other.maintenance();
  expect((await current("c")).status).toBe("running");
  await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(3));
});

it("closes aged legacy waits and exhausted rounds with an unknown effect and no new AIS", async () => {
  await enable();
  const old = now - 24 * hour;
  await seed("aged", { effectWait: undefined, observationDeadlineAt: old,
    timeline: [{ event_id: "old-observation", at_ms: old, kind: "observation", actor: "agent", summary: "still waiting", payload: {} }] });
  await seed("exhausted", { effectWait: { firstAt: now - 2 * hour, sinceAt: now, resumes: 3 } });
  await seed("prod", { controlPlaneEnvironment: "prod", effectWait: { firstAt: old, sinceAt: old, resumes: 0 } });
  await service.maintenance();
  for (const id of ["aged", "exhausted"]) {
    const task = await current(id);
    expect(task.status).toBe("completed");
    expect(task.judgment).toMatchObject({ effect: "unknown", delivery: "confirmed", response: "acted" });
    expect(task.pending_results.at(-1)).toMatchObject({ status: "completed", effect: "unknown" });
  }
  expect((await current("prod")).status).toBe("waiting_effect");
  expect(start).not.toHaveBeenCalled();
});

it("starts a fresh delay on each wait and closes after the third automatic observation", async () => {
  await enable(1);
  await seed("rounds");
  const firstAt = (await current("rounds")).effectWait!.firstAt;
  for (let round = 1; round <= 3; round++) {
    await service.maintenance();
    let task = await current("rounds");
    expect(task.status).toBe("running");
    expect(task.effectWait).toMatchObject({ firstAt, resumes: round });
    await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(round));
    const observationEnds = now + hour;
    await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
      requiresFreshObservation: false, observationDeadlineAt: observationEnds }));
    now = observationEnds;
    task = await current("rounds");
    await service.observe(task.taskId, task.version, { ...unknown, evidence_ids: [] }, []);
    expect((await current("rounds")).effectWait?.sinceAt).toBe(now);
    if (round < 3) {
      now += hour - 1;
      await service.maintenance();
      expect(start).toHaveBeenCalledTimes(round);
      now++;
    }
  }
  await service.maintenance();
  await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(3));
  expect(await current("rounds")).toMatchObject({ status: "completed", judgment: { effect: "unknown" } });
});

it("does not hold the next maintenance scan while a resumed AIS is being dispatched", async () => {
  await enable(1);
  await seed("deferred");
  let finish!: () => void;
  start.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  try {
    await service.maintenance();
    await vi.waitFor(() => expect(start).toHaveBeenCalledOnce());
    await seed("fresh", { status: "running", phase: "assess", effectWait: undefined });
    await service.maintenance();
    expect(start.mock.calls.map(([task]) => task.taskId)).toEqual(["deferred", "fresh"]);
  } finally {
    finish?.();
  }
});

it("releases the slot when a registered execution is canceled before dispatch after a restart", async () => {
  await enable(1);
  const identity = { taskId: "interrupted", executionId: "exec-interrupted", executionNo: 1, handler: "session_recovery" };
  const contract = agentExecutionContract(identity);
  const executor = new AgentExecutionService({ nowSeconds: () => Math.floor(now / 1000), leaseSeconds: 120 });
  await seed(identity.taskId, { status: "running", effectResumeSlot: { firstExecutionNo: 1 },
    executionNo: 1, execution: executor.issue(identity.executionId, contract.stepId).execution, aisExecution: contract });
  await createAgentExecutionStep(repo, identity);
  await seed("next");
  const ais = { execute: vi.fn(), stopExecution: vi.fn(), getJobStatus: vi.fn(), getJobStatusDetail: vi.fn() };
  const worker = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo,
    audit: new AgentToolCallRepository(db, "session_recovery"), ais,
    runtime: { request: vi.fn(), executeShell: vi.fn(), sendMessage: vi.fn() }, targets: { resolve: vi.fn() },
    business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1,
    nasRoots: [], leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: hour, now: () => now });
  const restart = new SessionRecoveryTaskService({ ...deps, agent: {
    ...deps.agent, terminate: task => worker.terminate(task), listExecutions: taskId => worker.listExecutions(taskId),
  } });
  const stopped = await restart.terminate(identity.taskId, { userId: "owner", isAdmin: false });
  expect(stopped.termination).toEqual({ status: "remote_stopped", aisJobId: null });
  expect(await tasks.claimEffectResumeDispatch(contract.stepId)).toBe(false);
  await restart.maintenance();
  expect((await current(identity.taskId)).effectResumeSlot).toBeUndefined();
  expect((await current("next")).status).toBe("running");
  expect(ais.stopExecution).not.toHaveBeenCalled();
});
