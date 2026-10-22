import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import type { RecoveryTaskResult } from "../task-contracts.js";

let db: SqliteDatabase, repo: EvolveRepository, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService;
let now: number, taskId: string, result: RecoveryTaskResult;
let artifacts: ConstructorParameters<typeof SessionRecoveryTaskRepository>[2];
let objects: Map<string, Buffer>;
const reporter = { report: vi.fn<(result: RecoveryTaskResult) => Promise<void>>() };
const agent = { start: vi.fn(), release: vi.fn(), terminate: vi.fn(), listExecutions: vi.fn() };
function worker(store = tasks, environment: "pre" | "prod" = "pre") {
  return new SessionRecoveryTaskService({ controlPlaneEnvironment: environment, tasks: store, agent,
    notifier: { notify: vi.fn() }, reporter, observationMs: 60000, now: () => now });
}
async function delivery() {
  const raw = JSON.parse((await repo.findTask(taskId))!.config_json);
  return raw.resultDelivery as { objectKey: string; sha256: string; resultVersion: number; attempts: number; retryAfterMs: number }[];
}
beforeEach(async () => {
  vi.resetAllMocks(); now = Date.now(); vi.spyOn(Date, "now").mockImplementation(() => now);
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite"); repo = new EvolveRepository(db);
  objects = new Map(); artifacts = {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
  };
  tasks = new SessionRecoveryTaskRepository(db, repo, artifacts); service = worker();
  reporter.report.mockRejectedValue(new Error("callback rejected"));
  const ack = await service.create({ event_id: "manual-case-without-diagnosis", tc_fault_label: "TC.MCP.CALL", delivery_key: "delivery-1", diagnosis: "工具调用受阻",
    session_key: "agent:main:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } }, { userId: "owner", isAdmin: false });
  taskId = ack.task_id;
  await vi.waitFor(() => expect(agent.start).toHaveBeenCalledTimes(1));
  await service.executionFailure(taskId, "已有业务结论，不得因为回传重试重新执行");
  result = (await tasks.find(taskId))!.pending_results[0];
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); });

it("backs off three times, stops after four attempts and retains the exact result and business state", async () => {
  const before = (await tasks.find(taskId))!;
  const ref = (await delivery())[0];
  for (const delay of [60000, 300000, 900000]) {
    const count = reporter.report.mock.calls.length;
    await service.reportResults();
    expect(reporter.report).toHaveBeenCalledTimes(count + 1);
    expect(reporter.report).toHaveBeenLastCalledWith(result);
    const [pending] = await delivery();
    expect(pending.attempts).toBe(count + 1); expect(pending.retryAfterMs).toBe(now + delay);
    now += delay - 1;
    const restarted = worker(new SessionRecoveryTaskRepository(db, repo, artifacts));
    await restarted.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(count + 1);
    now += 1;
  }
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(4);
  expect(await delivery()).toEqual([]);
  now += 86400000; await worker().reportResults(); expect(reporter.report).toHaveBeenCalledTimes(4);
  const after = (await tasks.find(taskId))!;
  expect(after).toMatchObject({ status: before.status, phase: before.phase, judgment: before.judgment, pending_results: [] });
  expect(after.timeline).toContainEqual(expect.objectContaining({ kind: "result", summary: expect.stringContaining("停止自动回传"),
    payload: expect.objectContaining({ result_version: result.result_version, attempts: 4, delivery_status: "exhausted" }) }));
  expect(JSON.parse(objects.get(ref.objectKey)!.toString())).toEqual(result);
  const step = (await repo.listSteps(taskId)).find(item => item.step_no === 1)!;
  const report = JSON.parse(objects.get(JSON.parse(step.output_json!).objectKey)!.toString());
  expect(report.results).toContainEqual(expect.objectContaining({ objectKey: ref.objectKey, resultVersion: result.result_version }));
  expect(agent.start).toHaveBeenCalledTimes(1);
});

it("retires an already exhausted historical callback without sending it again", async () => {
  const raw = JSON.parse((await repo.findTask(taskId))!.config_json);
  raw.resultDelivery[0].attempts = 46018;
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(raw), taskId]);
  await worker(tasks, "prod").reportResults(); expect(await delivery()).toHaveLength(1);
  await service.reportResults(); expect(reporter.report).not.toHaveBeenCalled(); expect(await delivery()).toEqual([]);
  expect((await tasks.find(taskId))!.timeline).toContainEqual(expect.objectContaining({ payload: expect.objectContaining({ attempts: 46018, delivery_status: "exhausted" }) }));
});

it("only lets one of two workers send a pending callback", async () => {
  let finish!: () => void;
  reporter.report.mockResolvedValue(undefined).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  const first = service.reportResults();
  await vi.waitFor(() => expect(reporter.report).toHaveBeenCalledTimes(1));
  try {
    const second = worker(new SessionRecoveryTaskRepository(db, repo, artifacts));
    // Include a stale hydrated task, not just the list's retry-time filter.
    await second.reportPending({ ...(await tasks.find(taskId))!, pending_results: [result] });
    expect(reporter.report).toHaveBeenCalledTimes(1);
  } finally { finish(); await first; }
  expect(await delivery()).toEqual([]);
});

it("removes a successfully delivered result and never retries it", async () => {
  reporter.report.mockResolvedValue(undefined);
  await service.reportResults(); await service.reportResults();
  expect(reporter.report).toHaveBeenCalledExactlyOnceWith(result);
  expect(await delivery()).toEqual([]);
  expect((await tasks.find(taskId))!.timeline).toContainEqual(expect.objectContaining({ summary: "诊断服务已接收任务结果" }));
});

it("reserves without object I/O and fences acknowledgments from an expired attempt", async () => {
  const get = vi.spyOn(artifacts, "getObject"), put = vi.spyOn(artifacts, "putObject");
  const first = await tasks.claimResult(taskId, result.result_version);
  expect(first).toBe(1); expect(get).not.toHaveBeenCalled(); expect(put).not.toHaveBeenCalled();
  expect(await tasks.claimResult(taskId, result.result_version)).toBeNull();
  now = (await delivery())[0].retryAfterMs;
  const second = await tasks.claimResult(taskId, result.result_version);
  expect(second).toBe(2);
  const reserved = await delivery();
  await tasks.acknowledgeResult(taskId, result.result_version, first!);
  await tasks.failResult(taskId, result.result_version, first!);
  expect(await delivery()).toEqual(reserved);
  await tasks.acknowledgeResult(taskId, result.result_version, second!);
  expect(await delivery()).toEqual([]);
});

it("lets exactly one competing version update reserve the same due result", async () => {
  const other = new SessionRecoveryTaskRepository(db, repo, artifacts);
  const claims = await Promise.all([tasks.claimResult(taskId, result.result_version), other.claimResult(taskId, result.result_version)]);
  expect(claims.filter(value => value === 1)).toHaveLength(1);
  expect(claims.filter(value => value === null)).toHaveLength(1);
  expect((await delivery())[0].attempts).toBe(1);
});

it("keeps a final in-flight attempt alive and retires it after its crash grace period", async () => {
  for (let attempt = 1; attempt <= 4; attempt++) {
    expect(await tasks.claimResult(taskId, result.result_version)).toBe(attempt);
    expect(await tasks.claimResult(taskId, result.result_version)).toBeNull();
    expect((await tasks.listForReporting(5, "", "pre")).tasks).toEqual([]);
    expect(await delivery()).toHaveLength(1);
    now = (await delivery())[0].retryAfterMs;
  }
  await service.reportResults();
  expect(reporter.report).not.toHaveBeenCalled(); expect(await delivery()).toEqual([]);
  expect((await tasks.find(taskId))!.timeline).toContainEqual(expect.objectContaining({ payload: expect.objectContaining({ attempts: 4, delivery_status: "exhausted" }) }));
});

async function addTask(index: number) {
  const ack = await service.create({ event_id: `queued-case-${index}`, tc_fault_label: "TC.MCP.CALL", delivery_key: `queued-${index}`, diagnosis: "工具调用受阻",
    session_key: "agent:main:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } }, { userId: "owner", isAdmin: false });
  await service.executionFailure(ack.task_id, "仅核对结果回传，不重做干预");
  return ack.task_id;
}

it("sends serially with a five-request tick budget and resumes the cursor without starving later tasks", async () => {
  for (let i = 0; i < 11; i++) await addTask(i);
  let active = 0, peak = 0;
  reporter.report.mockImplementation(async () => {
    active += 1; peak = Math.max(peak, active);
    await new Promise(resolve => setImmediate(resolve)); active -= 1;
  });
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(5);
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(10);
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(12);
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledTimes(12);
  expect(peak).toBe(1);
  expect(new Set(reporter.report.mock.calls.map(([value]) => value.task_id)).size).toBe(12);
});

it("reads only one candidate page per tick and skips delayed tasks without hydrating their artifacts", async () => {
  for (let i = 0; i < 6; i++) await addTask(i);
  const rows = await db.query<{ task_id: string; config_json: string }>("SELECT task_id, config_json FROM ce_tasks ORDER BY task_id");
  for (const row of rows) {
    const config = JSON.parse(row.config_json); config.resultDelivery[0].retryAfterMs = now + 900000;
    await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(config), row.task_id]);
  }
  await new Promise(resolve => setImmediate(resolve));
  const get = vi.spyOn(artifacts, "getObject"), query = vi.spyOn(db, "query");
  const page = await tasks.listForReporting(5, "", "pre");
  expect(page.tasks).toEqual([]); expect(page.nextTaskId).toBe(rows[4].task_id);
  expect(query).toHaveBeenCalledTimes(1); expect(get).not.toHaveBeenCalled();
  expect((await tasks.listForReporting(5, page.nextTaskId, "pre")).nextTaskId).toBe("");
});

it("does not recreate an exhausted callback when the task is subsequently saved", async () => {
  const raw = JSON.parse((await repo.findTask(taskId))!.config_json); raw.resultDelivery[0].attempts = 50000;
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(raw), taskId]);
  await service.reportResults();
  const current = (await tasks.find(taskId))!;
  await tasks.update(taskId, current.version, value => ({ ...value, version: value.version + 1, shared: true }));
  await worker().reportResults();
  expect(reporter.report).not.toHaveBeenCalled(); expect(await delivery()).toEqual([]);
  const state = (await tasks.find(taskId))!;
  expect(state.timeline.filter(event => event.event_id === `result-stopped-${result.result_version}`)).toHaveLength(1);
  expect(state.shared).toBe(true);
});

it("continues with a newer saved result after retiring an exhausted head", async () => {
  const current = (await tasks.find(taskId))!;
  const next = { ...result, result_version: result.result_version + 1 };
  await tasks.update(taskId, current.version, value => ({ ...value, version: value.version + 1, pending_results: [...value.pending_results, next] }));
  const raw = JSON.parse((await repo.findTask(taskId))!.config_json); raw.resultDelivery[0].attempts = 50000;
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(raw), taskId]);
  reporter.report.mockResolvedValue(undefined);
  await service.reportResults(); expect(reporter.report).not.toHaveBeenCalled();
  await service.reportResults(); expect(reporter.report).toHaveBeenCalledExactlyOnceWith(next);
  expect(await delivery()).toEqual([]);
});
