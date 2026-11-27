import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { RecoveryResultCallbackError } from "../errors.js";
import { logRepairEvent } from "../../common/logging.js";
import type { RecoveryTaskResult } from "../task-contracts.js";
vi.mock("../../common/logging.js", async original => ({
  ...await original<typeof import("../../common/logging.js")>(), logRepairEvent: vi.fn(),
}));

let db: SqliteDatabase, repo: EvolveRepository, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService;
let taskId: string, objects: Map<string, Buffer>;
const flush = () => new Promise(resolve => setImmediate(resolve));
const reporter = { report: vi.fn<(result: RecoveryTaskResult) => Promise<void>>() };
const agent = { start: vi.fn(), release: vi.fn(), terminate: vi.fn(), listExecutions: vi.fn() };
const worker = () => new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent,
  notifier: { notify: vi.fn() }, reporter, observationMs: 60000, now: () => Date.now() });
const current = async () => (await tasks.find(taskId))!;
async function snapshot() {
  const step = (await repo.listSteps(taskId)).find(item => item.step_no === 1)!;
  const report = JSON.parse(objects.get(JSON.parse(step.output_json!).objectKey)!.toString());
  const ref = report.results.at(-1);
  return { ref, result: JSON.parse(objects.get(ref.objectKey)!.toString()) as RecoveryTaskResult };
}
beforeEach(async () => {
  vi.resetAllMocks();
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite"); repo = new EvolveRepository(db);
  objects = new Map();
  tasks = new SessionRecoveryTaskRepository(db, repo, {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
  });
  service = worker(); reporter.report.mockResolvedValue(undefined);
  const ack = await service.create({ event_id: "case", tc_fault_label: "TC.MCP.CALL", delivery_key: "delivery-1", diagnosis: "工具调用受阻",
    session_key: "agent:main:original", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" } }, { userId: "owner", isAdmin: false });
  taskId = ack.task_id;
  await flush();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});
afterEach(async () => { await service.dispose(); vi.useRealTimers(); vi.restoreAllMocks(); await db.close(); });

it("sends once immediately after state and immutable result commit, without a Task delivery queue", async () => {
  reporter.report.mockImplementation(async result => {
    const row = (await repo.findTask(taskId))!;
    expect(row.status).toBe("failed");
    expect((await snapshot()).result).toEqual(result);
  });
  await service.executionFailure(taskId, "业务失败"); await flush();
  expect(reporter.report).toHaveBeenCalledOnce();
  const config = JSON.parse((await repo.findTask(taskId))!.config_json);
  for (const field of ["resultDelivery", "stoppedResultDelivery", "pending_results", "resultToReport"]) expect(config).not.toHaveProperty(field);
  expect((await current()).timeline).toContainEqual(expect.objectContaining({ event_id: `result-${config.version}`, payload: { result_version: config.version, attempts: 1 } }));
  await service.maintenance(); await vi.advanceTimersByTimeAsync(86400000);
  expect(reporter.report).toHaveBeenCalledOnce();
});
it("backs off for 1, 5 and 15 minutes, then stops after four attempts with the original snapshot", async () => {
  reporter.report.mockRejectedValue(new RecoveryResultCallbackError({ errorCode: "RECOVERY_RESULT_HTTP_REJECTED",
    failureKind: "http", httpStatus: 404, upstreamCode: "MONITORING_RESULT_TARGET_NOT_FOUND", reason: "结果回传被 HTTP 服务拒绝" }));
  await service.executionFailure(taskId, "已有业务结论"); await flush();
  const before = await current(), { result } = await snapshot();
  expect(reporter.report).toHaveBeenCalledTimes(1);
  for (const [index, delay] of [60000, 300000, 900000].entries()) {
    await vi.advanceTimersByTimeAsync(delay - 1); expect(reporter.report).toHaveBeenCalledTimes(index + 1);
    await vi.advanceTimersByTimeAsync(1); await flush(); expect(reporter.report).toHaveBeenCalledTimes(index + 2);
  }
  await vi.advanceTimersByTimeAsync(86400000);
  expect(reporter.report.mock.calls.map(([sent]) => sent)).toEqual([result, result, result, result]);
  const after = await current();
  expect(after).toMatchObject({ status: before.status, judgment: before.judgment, version: before.version });
  expect(after.timeline).toContainEqual(expect.objectContaining({ event_id: `result-stopped-${result.result_version}`,
    payload: { result_version: result.result_version, attempts: 4, delivery_status: "exhausted" } }));
  const failures = vi.mocked(logRepairEvent).mock.calls.filter(([, , event]) => event === "callback_failed").map(call => call[3]!);
  expect(failures).toHaveLength(4);
  expect(failures[0]).toMatchObject({ taskId, eventId: "case", stateVersion: result.result_version, attempt: 1,
    failureKind: "http", httpStatus: 404, upstreamCode: "MONITORING_RESULT_TARGET_NOT_FOUND", elapsedMs: expect.any(Number), retryAtMs: expect.any(Number) });
  expect(failures[3]).not.toHaveProperty("retryAtMs");
  expect(agent.start).toHaveBeenCalledOnce();
});
it("stops retrying after success and records the successful attempt", async () => {
  reporter.report.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined);
  await service.executionFailure(taskId, "failed"); await flush();
  await vi.advanceTimersByTimeAsync(60000); await flush();
  await vi.advanceTimersByTimeAsync(86400000);
  expect(reporter.report).toHaveBeenCalledTimes(2);
  expect((await current()).timeline.find(event => event.kind === "result")?.payload.attempts).toBe(2);
});
it("does not send if committing the Step and business state fails", async () => {
  const before = await repo.findTask(taskId);
  await db.exec("CREATE TRIGGER reject_result BEFORE UPDATE OF output_json ON ce_steps BEGIN SELECT RAISE(ABORT, 'write failed'); END");
  await expect(service.executionFailure(taskId, "failed")).rejects.toThrow("write failed");
  expect((await repo.findTask(taskId))!.config_json).toBe(before!.config_json);
  expect(reporter.report).not.toHaveBeenCalled();
});
it("sends a newer result while another callback is slow and never mutates the frozen snapshot", async () => {
  let release!: () => void;
  reporter.report.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  await service.executionFailure(taskId, "failed");
  const old = await current();
  const newer = { ...(await snapshot()).result, result_version: old.version + 1, result_text: "new conclusion" };
  const saved = await tasks.update(taskId, old.version, value => ({ ...value, version: value.version + 1, resultToReport: newer }));
  service.reportResult(saved!, newer);
  newer.result_text = "mutated after dispatch";
  try {
    expect(reporter.report).toHaveBeenCalledTimes(2);
    expect(reporter.report.mock.calls[1][0].result_text).toBe("new conclusion");
    await flush();
    expect((await current()).timeline.some(event => event.event_id === `result-${newer.result_version}`)).toBe(true);
  } finally { release(); await service.dispose(); }
  expect((await current()).timeline.filter(event => event.kind === "result")).toHaveLength(2);
});
it("cancels backoff on shutdown and does not replay results after restart", async () => {
  reporter.report.mockRejectedValue(new Error("offline"));
  await service.executionFailure(taskId, "failed"); await flush();
  await service.dispose();
  service = worker();
  await service.maintenance(); await vi.advanceTimersByTimeAsync(86400000); await flush();
  expect(reporter.report).toHaveBeenCalledOnce();
  expect((await current()).timeline.filter(event => event.kind === "result")).toEqual([]);
});
it("drains in-flight HTTP and outcome persistence before dispose resolves", async () => {
  let finish!: () => void;
  reporter.report.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  await service.executionFailure(taskId, "failed");
  let disposed = false;
  const closing = service.dispose().then(() => { disposed = true; });
  await flush(); expect(disposed).toBe(false);
  finish(); await closing;
  expect((await current()).timeline.some(event => event.kind === "result")).toBe(true);
});
it("logs audit persistence failure separately and never retries an accepted callback", async () => {
  vi.spyOn(tasks, "appendResultOutcome").mockRejectedValue(new Error("audit unavailable"));
  await service.executionFailure(taskId, "failed"); await flush();
  await vi.advanceTimersByTimeAsync(86400000);
  expect(reporter.report).toHaveBeenCalledOnce();
  expect(logRepairEvent).toHaveBeenCalledWith("session-recovery", "session_recovery_reporting", "callback_audit_failed",
    expect.objectContaining({ status: "accepted", reason: "audit unavailable" }));
  expect(vi.mocked(logRepairEvent).mock.calls.some(call => call[2] === "callback_failed")).toBe(false);
});
it("preserves old exhausted-result audit after a normal write removes old delivery metadata", async () => {
  await service.executionFailure(taskId, "failed"); await flush();
  const { ref, result } = await snapshot();
  const config = JSON.parse((await repo.findTask(taskId))!.config_json);
  config.stoppedResultDelivery = [{ ...ref, resultVersion: result.result_version, attempts: 4, stoppedAtMs: Date.now() }];
  await db.exec("UPDATE ce_tasks SET config_json=? WHERE task_id=?", [JSON.stringify(config), taskId]);
  const before = await current();
  await tasks.update(taskId, before.version, value => ({ ...value, version: value.version + 1 }));
  expect(JSON.parse((await repo.findTask(taskId))!.config_json)).not.toHaveProperty("stoppedResultDelivery");
  expect((await current()).timeline.filter(event => event.event_id === `result-stopped-${result.result_version}`)).toHaveLength(1);
  expect(reporter.report).toHaveBeenCalledOnce();
});
