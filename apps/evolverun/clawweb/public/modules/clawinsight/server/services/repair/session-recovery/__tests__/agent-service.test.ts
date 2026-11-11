import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import { logRepairEvent } from "../../common/logging.js";
vi.mock("../../common/logging.js", async importOriginal => ({
  ...await importOriginal<typeof import("../../common/logging.js")>(), logRepairEvent: vi.fn(),
}));

const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "dispatch-fixture", tc_fault_label: null, delivery_key: "initial-1", diagnosis: "原任务执行受阻",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
let db: SqliteDatabase, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService;
let ais: AisExecutor, runtime: BotRuntime, target: ResolvedBotTarget;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  const repo = new EvolveRepository(db), objects = new Map<string, Buffer>();
  tasks = new SessionRecoveryTaskRepository(db, repo, {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
  });
  target = { provider: "arca", environment: "prod", ownerId: "owner", botId: "default",
    deviceId: "fixture", bindingId: "binding", sandboxId: "sandbox", activeEngine: "openclaw" };
  runtime = { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() };
  ais = { execute: vi.fn<AisExecutor["execute"]>().mockResolvedValueOnce("job-1").mockResolvedValueOnce("job-2"),
    getJobStatus: vi.fn(), getJobStatusDetail: vi.fn(), stopExecution: vi.fn() };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo,
    audit: new AgentToolCallRepository(db, "session_recovery"), ais, runtime, targets: { resolve: async () => target },
    business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1,
    leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000 });
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent,
    notifier: { notify: vi.fn() }, reporter: { report: vi.fn() }, observationMs: 3600000,
    resolveTarget: async () => recoveryTargetSnapshot(target) });
});
afterEach(() => { db.close(); vi.restoreAllMocks(); });

it.each([
  { engine: "OC", activeEngine: "openclaw", provider: "baas" },
  { engine: "CC", activeEngine: "claude_code", provider: "baas" },
  { engine: "TE", activeEngine: "teclaw", provider: "teclaw" },
])("sends initial $engine guidance to the original session after a version-only release change", async ({ engine, activeEngine, provider }) => {
  target = { ...target, provider, activeEngine, botType: "service", runtimeIdentity: { stage: "online", version: "V7" } };
  const ack = await service.create({ ...request, target: { ...request.target, engine } }, actor);
  await vi.waitFor(async () => expect((await tasks.find(ack.task_id))?.execution?.jobId).toBe("job-1"));
  const frozen = (await tasks.find(ack.task_id))!;
  target = { ...target, runtimeIdentity: { stage: "online", version: "V8" } };
  vi.mocked(runtime.sendMessage).mockResolvedValue({ status: "accepted", platformMessageId: "message-1" });

  const result = await agent.retryMessage(frozen, { clientRequestId: "version-change", purpose: "继续原任务", message: "请核实已有结果后继续" });

  expect(result).toMatchObject({ status: "succeeded", result: { status: "accepted", initial_message: true } });
  expect(runtime.sendMessage).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    target: expect.objectContaining(target), expectedEngine: activeEngine,
    sessionKey: request.session_key, deliveryKey: request.delivery_key,
  }));
  expect((await tasks.find(ack.task_id))?.runtimeTarget).toEqual(frozen.runtimeTarget);
});

it.each([
  { change: { bindingId: "binding-new" }, detail: "运行绑定：binding → binding-new" },
  { change: { deviceId: "device-new" }, detail: "运行设备：fixture → device-new" },
  { change: { runtimeIdentity: { stage: "online", version: "V8" }, bindingId: "binding-new" }, detail: "运行绑定：binding → binding-new" },
])("records the actual target change and an unsent receipt without weakening the guard: $detail", async ({ change, detail }) => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw", botType: "service", runtimeIdentity: { stage: "online", version: "V7" } };
  const ack = await service.create({ ...request, target: { ...request.target, engine: "TE" } }, actor);
  await vi.waitFor(async () => expect((await tasks.find(ack.task_id))?.execution?.jobId).toBe("job-1"));
  const frozen = (await tasks.find(ack.task_id))!;
  target = { ...target, ...change } as ResolvedBotTarget;
  const result = await agent.retryMessage(frozen, { clientRequestId: "target-change", purpose: "继续原任务", message: "请核实已有结果后继续" });
  expect(result).toMatchObject({ status: "failed", result: { dispatched: false, error: { code: "recovery_execution_conflict", message: expect.stringContaining(detail) } } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
  const saved = (await tasks.find(ack.task_id))!;
  expect(saved.runtimeTarget).toEqual(frozen.runtimeTarget);
  expect(saved.timeline.find(event => event.event_id === result.toolCallId)?.payload.result).toMatchObject({ dispatched: false, error: { message: expect.stringContaining(detail) } });
});

it("provides cumulative read ranges and gaps without rereading the Bot or changing task state", async () => {
  const ack = await service.create(request, actor);
  await vi.waitFor(async () => expect((await tasks.find(ack.task_id))?.execution?.jobId).toBe("job-1"));
  const task = (await tasks.find(ack.task_id))!;
  const page = (lines: number[]) => ({ status: "success" as const, exitCode: 0, stderr: "", durationMs: 1,
    stdout: JSON.stringify({ session_id: "original", messages: lines.map(line_no => ({ line_no, record: { type: "message" } })), next_line: lines.at(-1)! + 1 }) });
  vi.mocked(runtime.executeShell).mockResolvedValueOnce(page([1, 2])).mockResolvedValueOnce(page([5, 6])).mockResolvedValueOnce(page([3, 4]));
  for (const [index, lines] of [[1, [1, 2]], [2, [5, 6]], [3, [3, 4]]] as const) {
    const receipt = await agent.sessionRead((await tasks.find(task.taskId))!, { clientRequestId: `page-${index}`, purpose: "读取原会话记录", from_line: lines[0] });
    if (index === 2) expect(receipt.result).toMatchObject({ session_read_coverage: [{ session_id: "original", provided_ranges: [[1, 2], [5, 6]], missing_ranges: [[3, 4]], scope: "provided_records_only" }] });
  }
  const saved = (await tasks.find(task.taskId))!;
  expect((await agent.bootstrap(saved)).task.sessionReadCoverage).toEqual([{ session_id: "original", provided_ranges: [[1, 6]], missing_ranges: [], scope: "provided_records_only" }]);
  expect(saved).toMatchObject({ phase: task.phase, status: task.status, version: task.version });
  expect(runtime.executeShell).toHaveBeenCalledTimes(3); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await tasks.update(saved.taskId, saved.version, value => ({ ...value, version: value.version + 1 }));
  const raw = (await db.query<{ config_json: string }>("SELECT config_json FROM ce_tasks WHERE task_id = ?", [task.taskId]))[0].config_json;
  expect(raw).not.toContain("sessionReadCoverage");
});

it.each([null, "TC.MCP.PERMISSION"])("sends only the task output-token override on initial and resumed dispatches (%s)", async tc_fault_label => {
  const output = vi.mocked(logRepairEvent).mockClear();
  const ack = await service.create({ ...request, tc_fault_label }, actor);
  await vi.waitFor(async () => expect((await tasks.find(ack.task_id))?.execution?.jobId).toBe("job-1"));
  for (const attempt of [1, 2]) {
    const task = (await tasks.find(ack.task_id))!;
    const [owner, params, snapshotId] = vi.mocked(ais.execute).mock.calls[attempt - 1];
    const envelope = JSON.parse(params["${clawevolve_params}"]);
    expect(owner).toBe(actor.userId); expect(snapshotId).toBe(1);
    expect(envelope).toEqual({ taskType: "session_recovery", taskId: task.taskId, stepId: task.aisExecution!.stepId, attempt,
      input: { taskId: task.taskId, executionId: task.execution!.executionId,
        toolsBaseUrl: `https://service.invalid/api/insight/v1/internal/monitoring/session-recoveries/${task.taskId}/executions/${task.execution!.executionId}`,
        credentials: { executionTicket: expect.any(String) } },
      runtime: { clawwebUrl: "https://service.invalid", package: { packageId: "clawevolve-session-recovery" }, llm: { max_tokens: 16384 } } });
    expect((await agent.verify(task.taskId, task.execution!.executionId, envelope.input.credentials.executionTicket)).taskId).toBe(task.taskId);
    if (attempt === 1) { await agent.release(task); await agent.start((await tasks.find(task.taskId))!); }
  }
  expect(ais.execute).toHaveBeenCalledTimes(2);
  expect(runtime.sendMessage).not.toHaveBeenCalled();
  expect(runtime.request).not.toHaveBeenCalled();
  expect(runtime.executeShell).not.toHaveBeenCalled();
  const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
  expect(events.filter(event => event.event === "dispatched")).toEqual([1, 2].map(attempt => expect.objectContaining({
    taskId: ack.task_id, executionId: expect.any(String), jobId: `job-${attempt}`, attempt, environment: "pre" })));
  expect(JSON.stringify(events)).not.toContain("executionTicket");
});

it.each(["observe", "verify", "assess"] as const)("releases a waiting %s execution and resumes without using a failure retry", async phase => {
  const { task_id } = await service.create({ ...request,
    ...(phase === "assess" ? { session_key: "agent:main:cron:daily:run:original" } : {}) }, actor);
  await vi.waitFor(async () => expect((await tasks.find(task_id))?.execution?.jobId).toBe("job-1"));
  const initial = (await tasks.find(task_id))!;
  const now = Date.now(), waiting = phase === "assess" ? { cronWait: { startedAt: now, deadlineAt: now + 300000 } }
    : { observationDeadlineAt: now + 3600000 };
  const task = (await tasks.update(task_id, initial.version, value => ({ ...value, phase, ...waiting, version: value.version + 1 })))!;
  const previousId = task.execution!.executionId;
  const repo = new EvolveRepository(db), step = (await repo.findStep(task.aisExecution!.stepId))!;

  await agent.report(task, { kind: "release", phase });
  const released = (await tasks.find(task_id))!;
  expect(released).toMatchObject({ status: "running", phase, ...waiting, execution: { executionId: previousId, state: "ended" } });
  expect(released.executionRetry).toBeUndefined();

  await service.maintenance();
  await agent.handleAisStatus({ task: (await repo.findTask(task_id))!, step, report: { status: "failed", retryable: true } });
  const resumed = (await tasks.find(task_id))!;
  expect(resumed).toMatchObject({ status: "running", phase, ...waiting, execution: { jobId: "job-2" } });
  expect(resumed.execution!.executionId).not.toBe(previousId);
  expect(resumed.executionRetry).toBeUndefined();
  expect(ais.execute).toHaveBeenCalledTimes(2);
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("rejects active release outside a waiting phase and when the phase changed before saving", async () => {
  const { task_id } = await service.create(request, actor);
  await vi.waitFor(async () => expect((await tasks.find(task_id))?.execution?.jobId).toBe("job-1"));
  const initial = (await tasks.find(task_id))!;
  await expect(agent.report(initial, { kind: "release", phase: "assess" })).rejects.toMatchObject({ status: 409 });
  const observing = (await tasks.update(task_id, initial.version, value => ({ ...value, phase: "observe", version: value.version + 1 })))!;
  await tasks.update(task_id, observing.version, value => ({ ...value, phase: "verify", version: value.version + 1 }));

  await expect(agent.report(observing, { kind: "release", phase: "observe" })).rejects.toMatchObject({ status: 409 });
  const current = (await tasks.find(task_id))!;
  expect(current.execution).toEqual(initial.execution);
  expect(current.executionRetry).toBeUndefined();
  expect(ais.execute).toHaveBeenCalledOnce();
});

it("counts an AIS failure once after committing its terminal task state despite repeated callbacks", async () => {
  const output = vi.mocked(logRepairEvent).mockClear();
  const { task_id } = await service.create(request, actor);
  await vi.waitFor(async () => expect((await tasks.find(task_id))?.execution?.jobId).toBe("job-1"));
  const task = (await tasks.find(task_id))!, repo = new EvolveRepository(db);
  const step = (await repo.findStep(task.aisExecution!.stepId))!;
  const report = { status: "failed" as const, errorCode: "TASK_TIMEOUT", errorMessage: "runtime interrupted", retryable: false };
  await agent.handleAisStatus({ task: (await repo.findTask(task_id))!, step, report });
  await agent.handleAisStatus({ task: (await repo.findTask(task_id))!, step: (await repo.findStep(step.step_id))!, report });
  const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
  expect(events.filter(event => event.event === "EXECUTION_FAILED")).toEqual([expect.objectContaining({
    taskId: task_id, executionId: task.execution!.executionId, jobId: "job-1", errorCode: "TASK_TIMEOUT", count: 1 })]);
  expect((await tasks.find(task_id))!.status).toBe("failed");
});

it("logs a failed read once from the settled tool audit without logging request or output bodies", async () => {
  const output = vi.mocked(logRepairEvent).mockClear();
  const { task_id } = await service.create(request, actor);
  await vi.waitFor(async () => expect((await tasks.find(task_id))?.execution?.jobId).toBe("job-1"));
  vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("reader unavailable; password=private-value"));
  const task = (await tasks.find(task_id))!, input = { clientRequestId: "read-1", purpose: "PRIVATE_PURPOSE" };
  const first = await agent.sessionRead(task, input), second = await agent.sessionRead(task, input);
  expect(first.status).toBe("failed"); expect(second.toolCallId).toBe(first.toolCallId);
  const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
  expect(events.filter(event => event.event === "tool_settled")).toEqual([expect.objectContaining({
    taskId: task_id, toolCallId: first.toolCallId, operation: "session_read", outcome: "failed" })]);
  expect(JSON.stringify(events)).not.toMatch(/PRIVATE_PURPOSE|private-value|PRIVATE_DIAGNOSIS/);
});

it.each([
  '{"success":false,"message":"PRIVATE_SESSION_BODY","data":{"executionTicket":"PRIVATE_TICKET"}}',
  '{"success":false,"message":"PRIVATE_SESSION_BODY"}',
])("does not log provider response bodies during dispatch retries or final failure: %s", async body => {
  const output = vi.mocked(logRepairEvent).mockClear();
  vi.mocked(ais.execute).mockReset().mockRejectedValue(new Error(`AIS executeSnapshot failed: ${body}`));
  const { task_id } = await service.create(request, actor);
  await vi.waitFor(async () => expect((await tasks.find(task_id))?.executionRetry?.failures).toBe(1));
  for (let attempt = 0; attempt < 2; attempt++) {
    const task = (await tasks.find(task_id))!;
    await tasks.update(task_id, task.version, value => ({ ...value, version: value.version + 1,
      executionRetry: { ...value.executionRetry!, retryAt: 1 } }));
    await agent.start((await tasks.find(task_id))!);
  }
  const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
  expect(events.filter(event => event.event === "dispatch_failed")).toHaveLength(3);
  expect(events.filter(event => event.event === "EXECUTION_FAILED")).toHaveLength(1);
  expect(JSON.stringify(events)).not.toMatch(/PRIVATE_SESSION_BODY|PRIVATE_TICKET/);
  expect((await tasks.find(task_id))!.status).toBe("failed");
});
