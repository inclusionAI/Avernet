import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, appendFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { BotRuntimeError } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import type { RecoveryTaskResult, RecoveryPlan, RecoveryStoredTask } from "../task-contracts.js";
const exec = promisify(execFile), flush = () => new Promise(resolve => setImmediate(resolve));
const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "event-fixture", tc_fault_label: "TC.MCP.PERMISSION", delivery_key: "initial-1", diagnosis: "原 MCP 操作失败", error_location: "原会话第1行",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
const plan: RecoveryPlan = { summary: "修正局部配置并重试", basis: "原工具调用失败，引用原会话证据", scope: "本任务目标的局部配置", verification: "原先失败的 MCP 操作成功并继续原任务",
  actions: [{ actionId: "fix", type: "container_command", operationScope: { operation: "file.write", target: "/workspace/memory.md" }, command: "printf repaired", summary: "修正配置", risk: "局部修改", rollback: "恢复原值", verification: "读取配置确认" }], owner_cooperation: [] };
let db: SqliteDatabase, repo: EvolveRepository, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService,
  runtime: BotRuntime, directory: string, sessionFile: string, now: number, target: ResolvedBotTarget, submissions: any[], callbacks: RecoveryTaskResult[], ais: any, notifier: any, actionNotifier: { after: ReturnType<typeof vi.fn> }, reporter: { report: (result: RecoveryTaskResult) => Promise<void> };
let artifactStore: ConstructorParameters<typeof SessionRecoveryTaskRepository>[2];
const current = async (id: string): Promise<RecoveryStoredTask> => (await tasks.find(id))!;
async function create(level = "approval", patch = {}) {
  const ack = await service.create({ ...request, execution_level: level, ...patch }, actor);
  await vi.waitFor(async () => {
    const saved = await current(ack.task_id);
    expect(saved.execution?.jobId).toBeTruthy();
    if (level !== "debug" && saved.phase !== "assess") expect(saved.delivery).toBeDefined();
  });
  return current(ack.task_id);
}
const inspect = (task: RecoveryStoredTask, key = "read-1") => agent.sessionRead(task, { clientRequestId: key, purpose: "观察原会话实际执行" });
async function toObserve(task: RecoveryStoredTask) {
  const sent = await agent.retryMessage(task, { clientRequestId: "initial-advice", purpose: "发送针对性继续建议", message: "请核对权限错误后继续原任务" });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "原Bot可以自行调整调用，已发送建议", evidence_ids: [sent.evidenceId] });
  return current(task.taskId);
}
async function toPlan(task: RecoveryStoredTask) {
  if (task.phase === "assess") task = await toObserve(task);
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment: { delivery: "confirmed", response: "none", effect: "not_recovered", summary: "原问题仍存在", evidence_ids: [read.evidenceId] } });
  return current(task.taskId);
}
beforeEach(async () => {
  now = Date.now(); submissions = []; callbacks = [];
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite"); repo = new EvolveRepository(db); const objects = new Map<string, Buffer>();
  artifactStore = { putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }) };
  tasks = new SessionRecoveryTaskRepository(db, repo, artifactStore);
  directory = await mkdtemp(join(tmpdir(), "recovery-session-fixture-")); const root = join(directory, "agents/main/sessions"); await mkdir(root, { recursive: true }); sessionFile = join(root, "original.jsonl");
  await writeFile(join(root, "sessions.json"), JSON.stringify({ [request.session_key]: { sessionId: "original", sessionFile: "original.jsonl" } }));
  await writeFile(sessionFile, JSON.stringify({ type: "message", id: "failed-operation", message: { role: "assistant", content: "MCP返回权限不足" } }) + "\n");
  target = { provider: "arca", environment: "prod", ownerId: "owner", botId: "default", deviceId: "fixture", bindingId: "binding", sandboxId: "sandbox", activeEngine: "openclaw" };
  runtime = { sendMessage: vi.fn(async input => { await appendFile(sessionFile, JSON.stringify({ type: "message", id: input.deliveryKey, message: { role: "user", content: input.message } }) + "\n"); return { status: "accepted" as const, platformMessageId: "provider-id" }; }), request: vi.fn(), executeShell: vi.fn(async input => {
    if (!input.command.startsWith("node -e")) return { status: "success" as const, exitCode: 0, stdout: "repaired", stderr: "", durationMs: 1 };
    const result = await exec("/bin/bash", ["-c", input.command], { env: { ...process.env, OPENCLAW_STATE_DIR: directory } }); return { status: "success" as const, exitCode: 0, stdout: result.stdout, stderr: result.stderr, durationMs: 1 };
  }) };
  const targets = { resolve: vi.fn(async () => target) };
  ais = { execute: vi.fn(async (_owner, params) => { submissions.push(JSON.parse(params["${clawevolve_params}"])); return `job-${submissions.length}`; }), getJobStatusDetail: vi.fn(async () => ({ status: "running", rawStatus: "RUNNING", errorMessage: null })), stopExecution: vi.fn(), getJobStatus: vi.fn() }; notifier = { notify: vi.fn() };
  actionNotifier = { after: vi.fn() };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"), ais, runtime, targets, business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1, nasRoots: [directory], leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now, actionNotifier });
  reporter = { report: async result => { callbacks.push(result); } };
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier, reporter, observationMs: 3600000, now: () => now, resolveTarget: async () => recoveryTargetSnapshot(target), actionNotifier });
});
afterEach(async () => { await db.close(); await rm(directory, { recursive: true, force: true }); });

it.each(["assess", "observe", "plan", "apply", "verify"] as const)("normally completes %s after user continuation without retries or a recovery claim", async phase => {
  let task = await create();
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "user-follow-up", message: { role: "user", content: "请继续只读检查，我来处理" } }) + "\n");
  const read = await inspect(task, "handoff-read");
  task = (await tasks.update(task.taskId, (await current(task.taskId)).version, value => ({ ...value, phase, version: value.version + 1 })))!;
  await agent.report(task, { kind: "blocked", reason: "user_continued", phase, summary: "原故障之后用户已继续对话，已让路", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", completionReason: "user_continued", interventionStatus: "none",
    observationDeadlineAt: null, ownerDeadlineAt: null, judgment: { effect: "unknown" }, execution: { state: "ended" } });
  expect(task.executionRetry).toBeUndefined(); expect(runtime.sendMessage).not.toHaveBeenCalled();
  expect(actionNotifier.after).toHaveBeenCalledWith(expect.objectContaining({ taskId: task.taskId, kind: "result" }));
  expect(await service.list(request.event_id, actor)).toContainEqual(expect.objectContaining({ completion_reason: "user_continued", status: "completed" }));
  await service.reportResults(); expect(callbacks).toHaveLength(1);
  expect(callbacks[0]).toMatchObject({ status: "completed", completion_reason: "user_continued", effect: "unknown", intervention_status: "none" });
  await service.executionFailure(task.taskId, "late AIS failure", task.execution!.executionId, true);
  await service.maintenance(); await service.reportResults();
  expect(submissions).toHaveLength(1); expect(callbacks).toHaveLength(1);
  expect((await current(task.taskId)).status).toBe("completed");
});

it("preserves actual intervention and effect evidence when a user takes over after delivery", async () => {
  let task = await toPlan(await create());
  const sent = vi.mocked(runtime.sendMessage).mock.calls.length;
  const read = await inspect(task, "user-takes-over"); task = await current(task.taskId);
  await agent.report(task, { kind: "blocked", phase: "plan", reason: "user_continued", summary: "用户已接手，本次不再操作", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", completionReason: "user_continued", interventionStatus: "performed", judgment: { effect: "not_recovered", delivery: "confirmed" } });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(sent); await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ completion_reason: "user_continued", effect: "not_recovered", intervention_status: "performed" });
});

it.each([[], ["missing-evidence"]])("rejects unsupported user continuation without changing the task", async evidence_ids => {
  const task = await create();
  await expect(agent.report(task, { kind: "blocked", phase: "assess", reason: "user_continued", summary: "用户已接手", evidence_ids })).rejects.toMatchObject({ status: 422 });
  expect((await current(task.taskId)).status).toBe("running");
});


it("rejects a stale execution's user handoff after another execution takes ownership", async () => {
  const stale = await create();
  const read = await inspect(stale, "stale-handoff-read");
  await tasks.update(stale.taskId, (await current(stale.taskId)).version, value => ({ ...value, version: value.version + 1,
    execution: { ...value.execution!, executionId: "replacement-execution" } }));
  await expect(agent.report(stale, { kind: "blocked", reason: "user_continued", phase: "assess", summary: "用户已继续", evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(await current(stale.taskId)).toMatchObject({ status: "running", execution: { executionId: "replacement-execution" } });
  expect(callbacks).toHaveLength(0);
});


it("ends normally for a proven destroyed target, preserving audit and unknown business effect", async () => {
  let task = await create();
  vi.mocked(runtime.request).mockRejectedValueOnce(new BotRuntimeError(410, "target_unavailable", "目标运行环境已销毁"));
  const read = await agent.inspect(task, { clientRequestId: "destroyed-status", purpose: "核查目标状态", operation: "engine_read", request: { method: "GET", path: "/api/engine/status" } });
  expect(read).toMatchObject({ status: "failed", result: { error: { code: "target_unavailable" } } });
  task = await current(task.taskId);
  await agent.report(task, { kind: "blocked", phase: "assess", reason: "target_unavailable", summary: "目标已销毁，无法继续调查或干预", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", completionReason: "target_unavailable", interventionStatus: "none", judgment: { effect: "unknown" }, execution: { state: "ended" } });
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "completed", completion_reason: "target_unavailable", effect: "unknown", intervention_status: "none" });
  await service.executionFailure(task.taskId, "late failed callback", task.execution!.executionId, true);
  await service.maintenance();
  expect(submissions).toHaveLength(1); expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("does not accept model-only target loss or an ordinary HTTP 404 as destroyed-target evidence", async () => {
  let task = await create();
  vi.mocked(runtime.request).mockResolvedValueOnce({ status: 404, body: { detail: "Session not found" } });
  const read = await agent.inspect(task, { clientRequestId: "ordinary-404", purpose: "核查状态", operation: "engine_read", request: { method: "GET", path: "/api/engine/status" } });
  task = await current(task.taskId);
  for (const evidence_ids of [[], [read.evidenceId]]) {
    await expect(agent.report(task, { kind: "blocked", phase: "assess", reason: "target_unavailable", summary: "推测目标已销毁", evidence_ids })).rejects.toMatchObject({ status: 422 });
  }
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "assess" });
});
