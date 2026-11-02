import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, appendFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import express from "express";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import { RecoverySessionReader } from "../session-reader.js";
import { SessionRecoveryGroupNotifier } from "../notification.js";
import { createSessionRecoveryRouter } from "../../../../routes/session-recovery.js";
import { reconcileAgentExecutions } from "../../common/agent-execution-monitor.js";
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
it("queries private event tasks for administrators while preserving Owner and sharing filters", async () => {
  const task = await create();
  expect((await service.list(request.event_id, { userId: "admin", isAdmin: true })).map(value => value.task_id)).toEqual([task.taskId]);
  expect(await service.list(request.event_id, { userId: "reader", isAdmin: false })).toEqual([]);
  expect(await service.list("other-event", { userId: "admin", isAdmin: true })).toEqual([]);
  await service.setTaskShared(task.taskId, true, actor);
  expect(await service.list(request.event_id, { userId: "reader", isAdmin: false })).toHaveLength(1);
});

it("continues Owner confirmation without recording a sent notification when disabled", async () => {
  let task = await toPlan(await create());
  notifier.notify.mockResolvedValue(false);
  await service.plan(task.taskId, task.version, plan);
  task = await current(task.taskId);
  expect(task.status).toBe("waiting_owner");
  expect(task.timeline.filter(event => event.kind === "notification")).toEqual([]);
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  expect((await current(task.taskId)).phase).toBe("apply");
});

it("persists deduplicated group delivery facts without changing the business version or exposing them to the Agent", async () => {
  const task = await create();
  const sent = await agent.retryMessage(task, { clientRequestId: "group-advice", purpose: "纠正原操作", message: "请按真实工具参数继续" });
  expect(actionNotifier.after).toHaveBeenCalledWith({ taskId: task.taskId, eventId: sent.toolCallId, kind: "message" });
  const before = await current(task.taskId);
  let finish!: () => void;
  const sender = { send: vi.fn(() => new Promise<void>(resolve => { finish = resolve; })) };
  const deps = { controlPlaneEnvironment: "pre" as const, tasks, sender, publicBaseUrl: "https://service.invalid",
    config: { findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ webhook: "https://robot.invalid/send", timeout_seconds: 10 }) }) } };
  const notice = { taskId: task.taskId, eventId: sent.toolCallId, kind: "message" as const };
  new SessionRecoveryGroupNotifier(deps).after(notice);
  new SessionRecoveryGroupNotifier(deps).after(notice);
  await vi.waitFor(() => expect(sender.send).toHaveBeenCalledOnce());
  expect((await current(task.taskId)).version).toBe(before.version);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message",
    summary: "已有现场证据，已发出指引", evidence_ids: [sent.evidenceId] });
  expect((await current(task.taskId)).phase).toBe("observe");
  finish();
  await vi.waitFor(async () => expect((await current(task.taskId)).timeline.some(event => event.payload.channel === "group_webhook" && event.payload.sent === true)).toBe(true));
  const after = await current(task.taskId);
  expect(after.version).toBe(before.version + 1);
  expect((await agent.bootstrap(after)).task.timeline.some(event => event.payload.channel === "group_webhook")).toBe(false);
  expect((await repo.findTask(task.taskId))!.config_json).not.toContain("https://robot.invalid");
});
it("persists sharing and authorizes read-only task links without granting Owner actions", async () => {
  const task = await create();
  const read = await inspect(task, "share-evidence");
  let principal: typeof actor | null = actor;
  const app = express();
  app.use(createSessionRecoveryRouter({ service, tasks: service, agent, resolveActor: async () => principal }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/monitoring/session-recoveries`;
  const call = async (path: string, method = "GET", body?: unknown) => {
    const response = await fetch(base + path, { method, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
  try {
    const version = (await current(task.taskId)).version;
    const scope = `/${task.taskId}`;
    expect(await call(scope + "/share", "PATCH", { shared: true })).toMatchObject({ status: 200, body: { shared: true, canOperate: true } });
    const restored = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), artifactStore);
    expect(await restored.find(task.taskId)).toMatchObject({ shared: true, version: version + 1, phase: "assess", status: "running", execution: task.execution });
    const raw = JSON.parse((await repo.findTask(task.taskId))!.config_json);
    expect(raw.shared).toBe(true);
    principal = { userId: "reader", isAdmin: false };
    expect(await call(`?event_id=${request.event_id}`)).toMatchObject({ status: 200, body: { items: [{ task_id: task.taskId }] } });
    const visible = await call(scope);
    expect(visible).toMatchObject({ status: 200, body: { shared: true, canOperate: false, canManageShare: false } });
    expect(visible.body.execution).toBeUndefined(); expect(visible.body.aisExecution).toBeUndefined();
    expect((await call(scope + `/tool-calls/${read.toolCallId}`)).status).toBe(200);
    for (const [suffix, method, body] of [
      ["/share", "PATCH", { shared: false }],
      ["/decision", "POST", { plan_digest: "digest", approved: true, cooperation_done: true }],
      ["/annotations", "POST", { layer: "effect", correct: true, reason: "read only", evidence_ids: [] }],
      ["/terminate", "POST", { reason: "read only" }],
    ] as const) expect((await call(scope + suffix, method, body)).status).toBe(403);
    principal = null;
    expect((await call(scope)).status).toBe(401);
    principal = actor;
    expect((await call(scope + "/share", "PATCH", { shared: "true" })).status).toBe(422);
    expect((await call(scope + "/share", "PATCH", { shared: false })).status).toBe(200);
    principal = { userId: "reader", isAdmin: false };
    expect((await call(scope)).status).toBe(403);
    expect((await call(scope + `/tool-calls/${read.toolCallId}`)).status).toBe(403);
    expect(await call(`?event_id=${request.event_id}`)).toMatchObject({ status: 200, body: { items: [] } });
    expect(runtime.sendMessage).not.toHaveBeenCalled();
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
it("Agent-first MCP sends one targeted message without an environment approval and recovers the original session", async () => {
  let task = await create("approval", {});
  expect(task.phase).toBe("assess"); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await service.maintenance(); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await expect(agent.apply(task, { actionId: "fix" })).rejects.toMatchObject({ status: 403 });
  const read = await inspect(task, "read-initial");
  const body = { clientRequestId: "initial-advice", purpose: "纠正原调用行为", message: "请依据实际 Schema 使用正确参数继续原步骤" };
  const sent = await agent.retryMessage(await current(task.taskId), body);
  expect(sent.status).toBe("succeeded"); expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(vi.mocked(runtime.sendMessage).mock.calls[0][0]).toMatchObject({ deliveryKey: request.delivery_key, sessionKey: request.session_key });
  expect(vi.mocked(runtime.sendMessage).mock.calls[0][0].message).not.toContain("clawevolve-recovery-handbook");
  await expect(agent.retryMessage(await current(task.taskId), { ...body, clientRequestId: "second-initial-advice" })).rejects.toMatchObject({ status: 409 });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "原Bot可自行纠正调用，已发送针对性建议", evidence_ids: [read.evidenceId, sent.evidenceId] });
  task = await current(task.taskId); expect(task.phase).toBe("observe"); expect(task.delivery!.delivery.delivery_key).toBe(request.delivery_key);
  expect((await agent.retryMessage(task, body)).toolCallId).toBe(sent.toolCallId); expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "agent-first-success", message: { role: "assistant", content: "原操作已成功，任务继续" } }) + "\n");
  const observed = await inspect(await current(task.taskId), "read-agent-first-effect");
  await agent.report(await current(task.taskId), { kind: "observation", phase: "observe", judgment: { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原失败步骤已成功", evidence_ids: [observed.evidenceId] } });
  expect((await current(task.taskId)).status).toBe("completed"); expect(notifier.notify).not.toHaveBeenCalled();
  expect(actionNotifier.after).toHaveBeenCalledWith(expect.objectContaining({ taskId: task.taskId, kind: "result" }));
  await service.reportResults(); expect(callbacks.at(-1)).toMatchObject({ effect: "recovered", delivery: { delivery_key: request.delivery_key } });
});
it("authorizes a terminal stop, invalidates tools, retries only the remote stop and reports cancellation once", async () => {
  const task = await create();
  await expect(service.terminate(task.taskId, { userId: "reader", isAdmin: false })).rejects.toMatchObject({ status: 403 });
  ais.stopExecution.mockRejectedValueOnce(new Error("offline"));
  const app = express(); app.use(createSessionRecoveryRouter({ service, tasks: service, agent, resolveActor: async () => actor }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  try {
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/monitoring/session-recoveries/${task.taskId}/terminate`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "暂停观察" }) });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "canceled", termination: { status: "remote_stop_failed", aisJobId: "job-1" } });
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
  const stopped = await current(task.taskId), version = stopped.version;
  expect(stopped.execution).toMatchObject({ state: "ended", invalidatedAt: Math.floor(now / 1000) });
  expect(stopped.timeline.find(event => event.kind === "termination")!.payload.actor_id).toBe("owner");
  const ticket = submissions[0].input.credentials.executionTicket;
  await expect(agent.verify(task.taskId, task.execution!.executionId, ticket)).rejects.toMatchObject({ status: 409 });
  expect(await agent.heartbeat(stopped)).toEqual({ state: "ended" });
  await expect(agent.retryMessage(task, { clientRequestId: "stopped-send", purpose: "继续", message: "继续" })).rejects.toMatchObject({ reason: "task_state" });
  expect(await service.terminate(task.taskId, { userId: "admin", isAdmin: true })).toMatchObject({ status: "canceled", termination: { status: "remote_stopped" } });
  expect((await current(task.taskId)).version).toBe(version);
  await service.maintenance(); expect(ais.execute).toHaveBeenCalledOnce(); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: (await repo.findStep(task.aisExecution!.stepId))!, report: { status: "failed", retryable: true } });
  await expect(service.resume(task.taskId)).rejects.toMatchObject({ status: 409 });
  expect((await current(task.taskId)).status).toBe("canceled");
  await service.reportResults(); await service.reportResults();
  expect(callbacks).toHaveLength(1); expect(callbacks[0]).toMatchObject({ status: "canceled", effect: "unknown", result_text: expect.stringContaining("暂停观察") });
});
it("stops a late AIS dispatch receipt instead of leaving a new Agent running", async () => {
  let finish!: (id: string) => void;
  ais.execute.mockImplementationOnce(async (_owner, params) => {
    submissions.push(JSON.parse(params["${clawevolve_params}"]));
    return new Promise<string>(resolve => { finish = resolve; });
  });
  const ack = await service.create(request, actor);
  await vi.waitFor(() => expect(submissions).toHaveLength(1));
  expect(await service.terminate(ack.task_id, actor)).toMatchObject({ status: "canceled", termination: { status: "stop_pending" } });
  finish("late-job");
  await vi.waitFor(() => expect(ais.stopExecution).toHaveBeenCalledWith("late-job", "owner"));
  await vi.waitFor(async () => expect((await service.detail(ack.task_id, actor)).termination?.status).toBe("remote_stopped"));
  expect((await current(ack.task_id)).status).toBe("canceled");
  expect((await repo.findStep((await current(ack.task_id)).aisExecution!.stepId))!.status).toBe("canceled");
});
it("retains an in-flight write as unknown after stopping without replay or task revival", async () => {
  const task = await create();
  let finish!: (result: { status: "accepted"; platformMessageId: string }) => void;
  vi.mocked(runtime.sendMessage).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const pending = agent.retryMessage(task, { clientRequestId: "in-flight", purpose: "纠正原操作", message: "继续原任务" });
  await vi.waitFor(() => expect(runtime.sendMessage).toHaveBeenCalledOnce());
  await service.terminate(task.taskId, actor);
  finish({ status: "accepted", platformMessageId: "late" });
  expect(await pending).toMatchObject({ status: "unknown" });
  expect((await current(task.taskId)).status).toBe("canceled");
  expect((await current(task.taskId)).timeline.some(event => event.kind === "operation" && event.payload.status === "unknown")).toBe(true);
  await service.reportResults(); expect(callbacks.at(-1)).toMatchObject({ status: "canceled", intervention_status: "unknown" });
  await service.maintenance(); expect(runtime.sendMessage).toHaveBeenCalledOnce();
});
it("non-MCP goes to one environment plan without any initial retry message", async () => {
  let task = await create("approval", { tc_fault_label: "TC.SKILL.EXECUTION" });
  const read = await inspect(task, "read-skill-failure");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "环境文件有确定错误，需修复后继续", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); expect(task.phase).toBe("plan"); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  expect(task.status).toBe("waiting_owner"); expect(notifier.notify).toHaveBeenCalledTimes(1);
  expect(actionNotifier.after).toHaveBeenCalledWith(expect.objectContaining({ taskId: task.taskId, kind: "plan" }));
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  await agent.apply(await current(task.taskId), { actionId: "fix", clientRequestId: "fix-skill", purpose: "执行获批环境修复" });
  await agent.retryMessage(await current(task.taskId), { clientRequestId: "retry-skill", purpose: "修复后继续原任务", message: "局部错误已修正，请继续原任务" });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});
it("trajectory content paging stays within the task scope and preserves the audited result", async () => {
  const read = vi.fn(async () => ({ source: "aivision_tclog", content: "正文片段", next_content_offset: 100 }));
  (agent as any).deps.trajectory = { read };
  const task = await create();
  const result = await agent.trajectoryRead(task, { clientRequestId: "trace-content", purpose: "读取故障事件输出", trace_id: "trace-1",
    observation_id: "event-1", field: "output", content_offset: 0, content_limit: 100 });
  expect(read).toHaveBeenCalledWith(expect.objectContaining({ target: task.runtimeTarget!.target, sessionKey: request.session_key,
    traceId: "trace-1", observationId: "event-1", field: "output", contentOffset: 0, contentLimit: 100, maxBytes: 128 * 1024 }));
  expect(result.status).toBe("succeeded");
  expect((await agent.toolGet(await current(task.taskId), result.toolCallId)).result).toEqual(result.result);
  await expect(agent.trajectoryRead(await current(task.taskId), { clientRequestId: "invalid-page", purpose: "读取轨迹", field: "output" })).rejects.toMatchObject({ status: 422 });
  await expect(agent.trajectoryRead(await current(task.taskId), { clientRequestId: "invalid-content", purpose: "读取轨迹", trace_id: "trace-1", field: "input", content_limit: 12001 })).rejects.toMatchObject({ status: 422 });
});
it("an oversized trajectory page is recorded as a readable failure instead of breaking audit persistence", async () => {
  const read = vi.fn().mockResolvedValueOnce({ content: "x".repeat(300000) }).mockResolvedValueOnce({ traces: [] });
  (agent as any).deps.trajectory = { read };
  const task = await create();
  const failed = await agent.trajectoryRead(task, { clientRequestId: "large-trace", purpose: "读取轨迹摘要" });
  expect(failed.status).toBe("failed");
  expect(failed.result).toMatchObject({ error: { code: "recovery_trajectory_page_too_large" } });
  expect((await agent.toolGet(await current(task.taskId), failed.toolCallId)).status).toBe("failed");
  const retried = await agent.trajectoryRead(await current(task.taskId), { clientRequestId: "smaller-trace", purpose: "缩小范围读取轨迹", limit: 1 });
  expect(retried.status).toBe("succeeded");
});
afterEach(async () => { await db.close(); await rm(directory, { recursive: true, force: true }); });
it("approves one plan in the same execution, retries the original session, recovers with durable evidence", async () => {
  let task = await toPlan(await create()); expect(submissions).toHaveLength(1); expect(submissions[0].input.credentials.executionTicket).toBeTruthy();
  await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId); expect(task.status).toBe("waiting_owner"); expect(notifier.notify).toHaveBeenCalledTimes(1);
  await expect(agent.apply(task, { actionId: "fix", clientRequestId: "fix-1", purpose: "修正配置" })).rejects.toMatchObject({ status: 403 });
  await service.decide(task.taskId, task.plan!.digest, true, actor); task = await current(task.taskId); expect(submissions).toHaveLength(1);
  const body = { actionId: "fix", clientRequestId: "fix-1", purpose: "修正配置" }, apply = await agent.apply(task, body); expect(apply.status).toBe("succeeded");
  expect((await agent.apply(await current(task.taskId), body)).toolCallId).toBe(apply.toolCallId);
  const retry = await agent.retryMessage(await current(task.taskId), { clientRequestId: "retry-1", purpose: "推动原会话重试", message: "配置已核验，请继续原任务" }); expect(retry.status).toBe("succeeded");
  await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: true, success: true, summary: "完成方案内全部操作" });
  expect((await current(task.taskId)).timeline.some(event => event.summary === "完成方案内全部操作")).toBe(true);
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "recovered-operation", message: { role: "assistant", content: "原MCP操作成功，继续原任务" } }) + "\n");
  const evidence = await inspect(await current(task.taskId), "read-verify"); expect((evidence.result as any).delivery.text).toContain("配置已核验");
  await agent.report(await current(task.taskId), { phase: "verify", kind: "observation", judgment: { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原失败操作已成功", evidence_ids: [evidence.evidenceId] } });
  await service.reportResults(); const view = await service.detail(task.taskId, actor); expect(view.status).toBe("completed"); expect(view.evidence).toHaveLength(5); expect(view.timeline.some(event => event.kind === "result")).toBe(true); expect(view.execution).toBeUndefined(); await service.reportResults(); expect(callbacks.at(-1)).toMatchObject({ status: "completed", effect: "recovered" }); expect(runtime.sendMessage).toHaveBeenCalledTimes(2);
  await service.annotate(task.taskId, actor, { layer: "effect", correct: true, reason: "原操作前后已核验", evidence_ids: [evidence.evidenceId] }); expect((await current(task.taskId)).timeline.some(event => event.kind === "annotation")).toBe(true);
});
it("Debug logs proposed actions without messages, writes or Owner notification", async () => {
  let task = await create("debug"); await expect(agent.apply(task, { actionId: "fix" })).rejects.toMatchObject({ status: 403 });
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "拟修复局部配置", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId); expect(task.status).toBe("completed"); expect(task.judgment!.effect).toBe("not_intervened"); expect(runtime.sendMessage).not.toHaveBeenCalled(); expect(notifier.notify).not.toHaveBeenCalled();
});
it.each(["succeeded", "failed"] as const)("Base release preserves Owner wait and later approval isolates an old %s callback", async status => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const oldStep = (await repo.findStep(task.aisExecution!.stepId))!, oldId = task.execution!.executionId;
  expect(task.ownerDeadlineAt).toBeNull();
  await agent.report(task, { kind: "release", phase: "apply" }); task = await current(task.taskId);
  expect(task.status).toBe("waiting_owner"); expect(task.execution!.state).toBe("ended");
  now += 12 * 3600000;
  await service.decide(task.taskId, task.plan!.digest, true, actor); task = await current(task.taskId);
  expect(submissions).toHaveLength(2); expect(task.execution!.executionId).not.toBe(oldId);
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: oldStep, report: { status, summary: "Base deadline graceful exit" } });
  expect((await current(task.taskId)).status).toBe("running");
  expect((await repo.findTask(task.taskId))!.status).toBe("running");
  expect((await current(task.taskId)).execution!.executionId).toBe(task.execution!.executionId);
  await service.reportResults(); expect(callbacks).toEqual([]);
});
it("Owner approval before Base release continues the live execution instead of ending it", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const oldId = task.execution!.executionId;
  await service.decide(task.taskId, task.plan!.digest, true, actor); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "release", phase: "apply" })).rejects.toMatchObject({ status: 409 });
  expect((await current(task.taskId)).execution!.state).not.toBe("ended");
  expect((await current(task.taskId)).execution!.executionId).toBe(oldId); expect(submissions).toHaveLength(1);
});
it("maintenance ignores the obsolete Owner deadline while the execution heartbeat is live", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const oldId = task.execution!.executionId;
  await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1, ownerDeadlineAt: now - 1 }));
  await service.maintenance(); task = await current(task.taskId);
  expect(task.status).toBe("waiting_owner"); expect(task.execution!.executionId).toBe(oldId); expect(task.execution!.state).not.toBe("ended");
  await service.reportResults(); expect(callbacks).toEqual([]);
});
it("NAS reads bind delivery to the current retry's actual user record, never an old marker or an assistant quote", async () => {
  let task = await toPlan(await create("automatic")); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  vi.mocked(runtime.sendMessage).mockResolvedValueOnce({ status: "accepted", platformMessageId: "new-retry" });
  await agent.retryMessage(task, { clientRequestId: "retry-after-plan", purpose: "重试原任务", message: "请重试" }); task = await current(task.taskId);
  const bootstrap = await agent.bootstrap(task), key = bootstrap.deliveryKey;
  expect(key).not.toBe(request.delivery_key);
  const evidence = { source: `${directory}/prod/prod_staff_owner_openclaw_default/.openclaw/agents/main/sessions/original.jsonl`, session_id: "original", delivery_key: key,
    delivery_confirmed: true, messages: [
      { line_no: 2, record: { type: "message", id: "old", message: { role: "user", content: `[会话自愈：${request.delivery_key}]\ncontinue` } } },
      { line_no: 3, record: { type: "message", id: "quoted", message: { role: "assistant", content: `[会话自愈：${key}]\nquote` } } },
    ] };
  expect(await agent.sessionRead(task, { clientRequestId: "stale-nas", purpose: "读取旧证据", nas_evidence: { ...evidence, delivery_key: request.delivery_key } })).toMatchObject({ status: "failed", result: { error: { code: "recovery_execution_conflict" } } });
  task = await current(task.taskId);
  const missing = await agent.sessionRead(task, { clientRequestId: "nas-current-missing", purpose: "核对新消息", nas_evidence: evidence });
  expect(missing.result).toMatchObject({ delivery_key: key, delivery_confirmed: false, delivery: null });
  await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: true, success: true, summary: "方案完成待验证" });
  task = await current(task.taskId);
  await expect(agent.report(task, { kind: "observation", phase: "verify", judgment: { delivery: "confirmed", response: "unknown", effect: "unknown", summary: "不能用旧消息证明新投递", evidence_ids: [missing.evidenceId] } })).rejects.toMatchObject({ status: 422 });
  evidence.messages.push({ line_no: 4, record: { type: "message", id: "actual", message: { role: "user", content: `[会话自愈：${key}]\nretry` } } });
  const found = await agent.sessionRead(task, { clientRequestId: "nas-current-found", purpose: "读取实际新消息", nas_evidence: evidence });
  expect(found.result).toMatchObject({ delivery_confirmed: true, delivery: { message_id: "actual" } });
});
it.each(["apply", "blocked"])("%s failure callbacks retain saved evidence without a prior effect judgment", async kind => {
  let task = await create("automatic"); const read = await inspect(task, "failure-source");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "需要环境修复", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const operation = await agent.apply(task, { clientRequestId: "apply-for-failure", purpose: "执行原方案", actionId: "fix" }); task = await current(task.taskId);
  await agent.report(task, kind === "apply" ? { kind, phase: "apply", applicable: false, success: false, summary: "方案不再适用" } : { kind, phase: "apply", summary: "执行受阻" });
  await service.reportResults(); const callback = callbacks.at(-1)!;
  expect(callback.status).toBe("failed"); expect(callback.effect).toBe("unknown");
  expect(callback.evidence_ids).toEqual(expect.arrayContaining([read.evidenceId, operation.evidenceId]));
  expect(callback.evidence?.some(item => item.tool_call_id === operation.toolCallId)).toBe(true);
});
it("effect waiting resumes the paused phase without resending advice", async () => {
  let task = await toObserve(await create()); const count = vi.mocked(runtime.sendMessage).mock.calls.length; expect(task.observationDeadlineAt).toBeTypeOf("number"); now = task.observationDeadlineAt! + 1; await service.maintenance(); task = await current(task.taskId); expect(task.status).toBe("waiting_effect"); await service.resume(task.taskId); task = await current(task.taskId); expect(task.phase).toBe("observe"); expect(task.requiresFreshObservation).toBe(true); await inspect(task, "read-resume"); expect((await current(task.taskId)).requiresFreshObservation).toBe(false); expect(runtime.sendMessage).toHaveBeenCalledTimes(count);
});
it("forced stop converges through the shared handler with unknown effect", async () => {
  let task = await create(); ais.getJobStatusDetail.mockResolvedValue({ status: "stopped", rawStatus: "STOPPED", errorMessage: null }); await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) }); task = await current(task.taskId); expect(task.status).toBe("failed"); expect(task.judgment!.effect).toBe("unknown"); expect(task.execution!.state).toBe("ended");
});
it("retries confirmed AIS startup failures in the same task, survives restart and reports only after exhaustion", async () => {
  let task = await create();
  const taskId = task.taskId, firstExecution = task.execution!.executionId, firstTicket = submissions[0].input.credentials.executionTicket;
  ais.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed", errorMessage: "failed to create task for container: context canceled" });
  const reconcile = () => reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  await reconcile(); task = await current(taskId);
  expect(task.status).toBe("running"); expect(task.execution!.state).toBe("ended");
  expect(task.timeline.some(event => event.summary.includes("context canceled"))).toBe(true);
  await expect(agent.verify(taskId, firstExecution, firstTicket)).rejects.toMatchObject({ status: 401 });
  await service.reportResults(); expect(callbacks).toEqual([]);
  await service.maintenance(); expect(submissions).toHaveLength(1);
  // Reconstruct the existing services over the same persistent task and ledger.
  tasks = new SessionRecoveryTaskRepository(db, repo, artifactStore);
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier, reporter, observationMs: 3600000, now: () => now });
  now += 20000;
  await Promise.all([service.maintenance(), service.maintenance()]);
  task = await current(taskId);
  expect(submissions).toHaveLength(2); expect(task.taskId).toBe(taskId); expect(task.execution!.executionId).not.toBe(firstExecution);
  const currentExecution = task.execution!.executionId;
  const oldStep = (await repo.findStep(`${taskId}-AIS-1`))!;
  await agent.handleAisStatus({ task: (await repo.findTask(taskId))!, step: oldStep, report: { status: "failed", retryable: true } });
  expect((await current(taskId)).execution!.executionId).toBe(currentExecution);
  await reconcile(); now += 59999; await service.maintenance(); expect(submissions).toHaveLength(2);
  now += 1; await service.maintenance(); expect(submissions).toHaveLength(3);
  await reconcile(); task = await current(taskId);
  expect(task.status).toBe("failed"); expect(task.judgment!.effect).toBe("unknown");
  await service.reportResults(); await service.maintenance(); await service.reportResults();
  expect(submissions).toHaveLength(3); expect(callbacks).toHaveLength(1);
  expect(callbacks[0]).toMatchObject({ task_id: taskId, status: "failed", effect: "unknown" });
  expect(callbacks[0].result_text).toContain("context canceled");
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});
it("retries dispatch failures with revoked tickets instead of failing the accepted task immediately", async () => {
  ais.execute.mockRejectedValueOnce(new Error("dispatch connection reset"));
  const ack = await service.create(request, actor); await flush();
  let task = await current(ack.task_id);
  expect(task.status).toBe("running"); expect(task.execution!.state).toBe("ended");
  await service.reportResults(); expect(callbacks).toEqual([]);
  now += 20000; await service.maintenance(); task = await current(ack.task_id);
  expect(task.execution!.jobId).toBeTruthy(); expect(ais.execute).toHaveBeenCalledTimes(2);
});
it("uses the original execution owner when an administrator stops recovery", async () => {
  const task = await create();
  await service.terminate(task.taskId, { userId: "admin", isAdmin: true });
  expect(ais.stopExecution).toHaveBeenCalledWith("job-1", "owner");
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});
it("keeps a rejected stop fenced and resumes the existing retry only after remote termination", async () => {
  let task = await create();
  const executionId = task.execution!.executionId;
  now = task.execution!.leaseExpiresAt * 1000 + 1;
  ais.stopExecution.mockRejectedValueOnce(new Error("temporary stop rejected"));
  const reconcile = () => reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo,
    handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais },
    nowSeconds: () => Math.floor(now / 1000) });
  await reconcile(); await service.maintenance(); task = await current(task.taskId);
  expect(task.execution!.executionId).toBe(executionId);
  expect(task.execution!.state).not.toBe("ended");
  expect(submissions).toHaveLength(1);
  expect(ais.stopExecution).toHaveBeenCalledWith("job-1", "owner");
  await reconcile(); await service.maintenance();
  expect((await current(task.taskId)).execution!.state).not.toBe("ended");
  expect(submissions).toHaveLength(1);
  ais.getJobStatusDetail.mockResolvedValue({ status: "stopped", rawStatus: "stopped", errorMessage: null });
  await reconcile(); now += 20000; await service.maintenance();
  expect(submissions).toHaveLength(2);
  expect((await current(task.taskId)).execution!.executionId).not.toBe(executionId);
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});
it("waits for the expired AIS job to stop before replacing its execution", async () => {
  let task = await create(); const executionId = task.execution!.executionId;
  now = task.execution!.leaseExpiresAt * 1000 + 1;
  await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  await service.maintenance(); task = await current(task.taskId);
  expect(ais.stopExecution).toHaveBeenCalledWith("job-1", "owner"); expect(submissions).toHaveLength(1);
  expect(task.status).toBe("running"); expect(task.execution!.executionId).toBe(executionId);
  ais.getJobStatusDetail.mockResolvedValue({ status: "stopped", rawStatus: "stopped", errorMessage: null });
  await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  now += 20000; await service.maintenance(); expect(submissions).toHaveLength(2);
});
it("preserves an approved plan and unknown write evidence across an AIS retry", async () => {
  let task = await toPlan(await create("automatic"));
  await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("write outcome unknown"));
  const applied = await agent.apply(task, { actionId: "fix", clientRequestId: "interrupted-write", purpose: "执行获批操作" });
  expect(applied.status).toBe("unknown");
  const operationCount = vi.mocked(runtime.executeShell).mock.calls.length;
  task = await current(task.taskId); const approved = task.plan, evidence = task.evidence;
  const step = (await repo.findStep(task.aisExecution!.stepId))!;
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step, report: { status: "failed", errorCode: "TASK_TIMEOUT", retryable: true } });
  ais.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed", errorMessage: null });
  now += 20000; await service.maintenance(); task = await current(task.taskId);
  expect(submissions).toHaveLength(2); expect(task).toMatchObject({ status: "running", phase: "apply", plan: approved, evidence });
  await expect(agent.apply(task, { actionId: "fix", clientRequestId: "blind-replay", purpose: "不能盲目重复写入" })).rejects.toMatchObject({ status: 409 });
  expect(runtime.executeShell).toHaveBeenCalledTimes(operationCount);
  await service.reportResults(); expect(callbacks).toEqual([]);
  await service.executionFailure(task.taskId, "后续执行结束"); await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "failed", intervention_status: "performed" });
});
it("does not retry a non-retryable Base failure or notify Owner about infrastructure failure", async () => {
  const task = await create(), step = (await repo.findStep(task.aisExecution!.stepId))!;
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step, report: { status: "failed", errorCode: "INVALID_INPUT", errorMessage: "invalid runtime input", retryable: false } });
  now += 120000; await service.maintenance(); await service.reportResults();
  expect(submissions).toHaveLength(1); expect(callbacks).toHaveLength(1);
  expect(callbacks[0]).toMatchObject({ status: "failed", effect: "unknown" });
  expect(callbacks[0].result_text).toContain("invalid runtime input"); expect(notifier.notify).not.toHaveBeenCalled();
});
it("a successful AIS retry continues the original workflow and sends only its final result", async () => {
  let task = await create();
  ais.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed", errorMessage: "container startup canceled" });
  await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  now += 20000; await service.maintenance();
  task = await toObserve(await current(task.taskId));
  const read = await inspect(task, "read-after-retry");
  await agent.report(await current(task.taskId), { kind: "observation", phase: "observe", judgment: {
    delivery: "confirmed", response: "acted", effect: "recovered", summary: "已核实原阻塞解除并继续推进", evidence_ids: [read.evidenceId] } });
  await service.reportResults();
  expect(submissions).toHaveLength(2); expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(callbacks).toHaveLength(1); expect(callbacks[0]).toMatchObject({ task_id: task.taskId, status: "completed", effect: "recovered" });
});
it("projects every AIS attempt and Host-provided job link without exposing execution credentials", async () => {
  ais.jobUrl = vi.fn((id: string) => `https://ais.example.test/jobs/${id}`);
  let task = await create(); const firstId = task.execution!.executionId;
  ais.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed", errorMessage: "container startup canceled" });
  await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  now += 20000; await service.maintenance(); task = await current(task.taskId);
  const view = await service.detail(task.taskId, actor);
  expect(view.executions).toEqual([
    { executionId: firstId, attempt: 1, status: "failed", jobId: "job-1", jobUrl: "https://ais.example.test/jobs/job-1" },
    { executionId: task.execution!.executionId, attempt: 2, status: "dispatched", jobId: "job-2", jobUrl: "https://ais.example.test/jobs/job-2" },
  ]);
  expect(view.timeline.find(event => event.summary.includes("container startup canceled"))!.payload.execution_id).toBe(firstId);
  expect(JSON.stringify(view.executions)).not.toMatch(/ticket|credentials|toolsBaseUrl/);
  delete ais.jobUrl;
  expect((await service.detail(task.taskId, actor)).executions.map(item => item.jobUrl)).toEqual([null, null]);
});
it("shows a failed dispatch without inventing an AIS job number or URL", async () => {
  ais.execute.mockRejectedValueOnce(new Error("dispatch unavailable"));
  const ack = await service.create(request, actor); await flush();
  expect((await service.detail(ack.task_id, actor)).executions).toEqual([
    { executionId: expect.any(String), attempt: 1, status: "failed", jobId: null, jobUrl: null },
  ]);
});
it("waits for an in-flight write lease and retains an abandoned write as unknown before retrying AIS", async () => {
  let task = await toPlan(await create("automatic"));
  await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const audit = new AgentToolCallRepository(db, "session_recovery");
  const input = { callId: "tc-abandoned-write", taskId: task.taskId, stepId: task.current!.stepId,
    executionId: task.execution!.executionId, authorizationScopeDigest: task.authorizationScopeDigest!,
    clientRequestId: "abandoned-write", toolName: "apply_action", operation: "apply_action", actionId: "fix", isWrite: true, request: { purpose: "原执行正在写入" } };
  await audit.createToolCall(input);
  await audit.claimToolCall({ ...input, leaseOwner: "old-worker", leaseExpiresAt: Math.floor(now / 1000) + 180 });
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: (await repo.findStep(task.aisExecution!.stepId))!,
    report: { status: "failed", errorCode: "TASK_TIMEOUT", retryable: true } });
  ais.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed", errorMessage: null });
  now += 20000; await service.maintenance(); expect(submissions).toHaveLength(1);
  expect((await audit.findToolCall(input.callId))!.status).toBe("executing");
  now += 180000; await service.maintenance(); task = await current(task.taskId);
  expect(submissions).toHaveLength(2); expect((await audit.findToolCall(input.callId))!.status).toBe("unknown");
  expect(task.evidence.some(item => item.tool_call_id === input.callId)).toBe(true);
  await expect(agent.apply(task, { actionId: "fix", clientRequestId: "new-write", purpose: "不能跳过未知写入" })).rejects.toMatchObject({ status: 409 });
});
it("a failed plan cannot submit a second automatic plan", async () => {
  let task = await toPlan(await create("automatic")); await agent.report(task, { kind: "plan", phase: "plan", plan }); await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: false, success: false, summary: "最新现场已变化，原方案不适用" }); task = await current(task.taskId); expect(task.status).toBe("failed"); await service.reportResults(); expect(callbacks.at(-1)!.effect).toBe("unknown"); await expect(service.plan(task.taskId, task.version, plan)).rejects.toMatchObject({ status: 409 });
});
it("uses the Engine reference when no actual request is supplied, only after approval", async () => {
  let task = await toPlan(await create());
  const enginePlan: RecoveryPlan = { ...plan, actions: [{ ...plan.actions[0], type: "engine_operation", operationScope: undefined, command: undefined,
    engineOperation: { method: "POST", path: "/api/file/upload", upload: { targetPath: "/workspace/memory.md", content: "approved content" } } }] };
  await agent.report(task, { kind: "plan", phase: "plan", plan: enginePlan });
  task = await current(task.taskId);
  const operation = { clientRequestId: "engine-write", purpose: "执行获批文件修改", actionId: "fix" };
  await expect(agent.apply(task, operation)).rejects.toMatchObject({ status: 403 });
  expect(runtime.request).not.toHaveBeenCalled();
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true } });
  const result = await agent.apply(await current(task.taskId), operation);
  expect(result.status).toBe("succeeded");
  expect(runtime.request).toHaveBeenCalledWith(expect.objectContaining({ target: expect.objectContaining(target), ...enginePlan.actions[0].engineOperation }));
  await agent.apply(await current(task.taskId), operation);
  expect(runtime.request).toHaveBeenCalledTimes(1);
});
it("TE rejects a Shell plan before granting any environmental operation", async () => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" }; let task = await create("approval", { target: { ...request.target, engine: "TE" } });
  (agent as any).deps.trajectory = { read: vi.fn().mockResolvedValue({ traces: [] }) };
  const evidence = await agent.trajectoryRead(task, { clientRequestId: "te-original", purpose: "调查原任务的环境阻塞" });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "根据诊断需形成环境方案", evidence_ids: [evidence.evidenceId] });
  task = await current(task.taskId);
  await expect(agent.report(task, { kind: "plan", phase: "plan", plan })).rejects.toMatchObject({ status: 422 });
  expect(runtime.executeShell).not.toHaveBeenCalled(); expect(runtime.request).not.toHaveBeenCalled();
  expect(notifier.notify).not.toHaveBeenCalled();
});
it("a delivery marker quoted in a tool result cannot confirm original-session delivery", async () => {
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "quoted", message: { role: "tool", content: "[会话自愈：initial-1]\n继续" } }) + "\n");
  const result = await new RecoverySessionReader(runtime).read({ target, sessionKey: request.session_key, deliveryKey: request.delivery_key });
  expect(result.delivery_confirmed).toBe(false); expect(result.delivery).toBeNull();
});
it("locates actual user advice after Engine metadata and preserves multiple matching records", async () => {
  const message = `Conversation info (untrusted metadata):\n{\"session\":\"test\"}\n[Mon 2026-10-12 12:30 GMT+8] [会话自愈：initial-1]\n继续`;
  for (const id of ["actual-delivery", "engine-replay"]) await appendFile(sessionFile, JSON.stringify({ type: "message", id, message: { role: "user", content: [{ type: "text", text: message }] } }) + "\n");
  const result = await new RecoverySessionReader(runtime).read({ target, sessionKey: request.session_key, deliveryKey: request.delivery_key });
  expect(result.delivery_confirmed).toBe(true);
  expect(result.delivery).toMatchObject({ message_id: "actual-delivery", text: message });
  expect(result.delivery_matches).toEqual([{ message_id: "actual-delivery", line_no: 2 }, { message_id: "engine-replay", line_no: 3 }]);
});
it("refuses to retry when the original-session index now points to another session", async () => {
  let task = await toPlan(await create("automatic")); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  await writeFile(join(directory, "agents/main/sessions/sessions.json"), JSON.stringify({ [request.session_key]: { sessionId: "replaced" } }));
  const result = await agent.retryMessage(task, { clientRequestId: "retry-replaced", purpose: "重试原任务", message: "继续" });
  expect(result.status).toBe("failed"); expect(result.result).toMatchObject({ dispatched: false, error: { code: "original_session_unavailable" } });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});
it("retains full operation evidence outside the bounded task excerpt", async () => {
  const task = await create(), large = "上下文".repeat(2000);
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "large-result", message: { role: "assistant", content: large } }) + "\n");
  const body = { clientRequestId: "read-1", purpose: "观察原会话实际执行", from_line: 1 };
  const read = await agent.sessionRead(task, body), saved = await current(task.taskId), evidence = saved.evidence.find(item => item.evidence_id === read.evidenceId)!;
  expect(evidence.excerpt.length).toBeLessThanOrEqual(2000);
  const raw = await agent.toolGet(saved, read.toolCallId); expect(JSON.stringify(raw.result)).toContain(large);
  const replay = await agent.sessionRead(saved, body); expect(replay.evidenceId).toBe(read.evidenceId);
  expect((await current(task.taskId)).evidence).toHaveLength(1);
});
it("accepts internal requests without independent tokens while execution tickets and Owner identity guard Agent and page operations", async () => {
  const app = express(); app.use(createSessionRecoveryRouter({ service, tasks: service, agent,
    resolveActor: async req => req.get("x-fixture-owner") ? { userId: req.get("x-fixture-owner")!, isAdmin: false } : null }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  try {
    const created = await fetch(`${origin}/internal/monitoring/session-recoveries`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
    expect(created.status).toBe(202);
    const ack = await created.json();
    await vi.waitFor(async () => expect((await current(ack.task_id)).execution?.jobId).toBeTruthy());
    const task = await toObserve(await current(ack.task_id)), input = submissions[0].input, worker = `${origin}/internal/monitoring/session-recoveries/${task.taskId}/executions/${task.execution!.executionId}`;
    expect((await fetch(`${origin}/internal/monitoring/session-recoveries/${task.taskId}/continue`, { method: "POST" })).status).toBe(409);
    const headers = { "Content-Type": "application/json", Authorization: `Bearer ${input.credentials.executionTicket}` };
    expect((await fetch(`${worker}/bootstrap`)).status).toBe(401);
    const bootstrap = await fetch(`${worker}/bootstrap`, { headers }); expect(bootstrap.status).toBe(200);
    expect((await bootstrap.json()).task.execution.ticketDigest).toBeUndefined();
    const read = await fetch(`${worker}/tools/session/read`, { method: "POST", headers, body: JSON.stringify({ clientRequestId: "http-read", purpose: "观察原故障及实际触达" }) });
    const evidence = await read.json(); expect(read.status).toBe(200); expect(evidence.result.delivery_confirmed).toBe(true);
    const ownerUrl = `${origin}/monitoring/session-recoveries/${task.taskId}`;
    expect((await fetch(`${ownerUrl}/tool-calls/${evidence.toolCallId}`)).status).toBe(401);
    expect((await fetch(ownerUrl, { headers: { "x-fixture-owner": "other" } })).status).toBe(403);
    const raw = await fetch(`${ownerUrl}/tool-calls/${evidence.toolCallId}`, { headers: { "x-fixture-owner": "owner" } });
    expect(raw.status).toBe(200); expect(JSON.stringify(await raw.json())).toContain("failed-operation");
    await agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment: { delivery: "confirmed", response: "none", effect: "not_recovered", summary: "原问题尚未解除", evidence_ids: [evidence.evidenceId] } });
    await agent.report(await current(task.taskId), { phase: "plan", kind: "plan", plan });
    const planned = await current(task.taskId), decision = JSON.stringify({ plan_digest: planned.plan!.digest, approved: true });
    expect((await fetch(`${ownerUrl}/decision`, { method: "POST", headers: { "Content-Type": "application/json", "x-fixture-owner": "other" }, body: decision })).status).toBe(403);
    expect((await fetch(`${ownerUrl}/decision`, { method: "POST", headers: { "Content-Type": "application/json", "x-fixture-owner": "owner" }, body: decision })).status).toBe(200);
    await agent.release(await current(task.taskId));
    expect((await fetch(`${worker}/tools/session/read`, { method: "POST", headers, body: JSON.stringify({ clientRequestId: "after-end", purpose: "已结束执行不能继续调查" }) })).status).toBe(401);
    expect((await fetch(`${worker}/bootstrap`, { headers })).status).toBe(200);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});

it("creates recovery tasks using the existing evolution-scoped artifact credentials", async () => {
  const put = artifactStore.putObject;
  artifactStore.putObject = async (key, content, contentType) => {
    if (!key.startsWith("evolution/")) throw new Error("AccessDenied: object is outside the credential scope");
    return put(key, content, contentType);
  };
  const task = await create();
  expect(task.status).toBe("running");
  expect(task.execution?.jobId).toBeTruthy();
  expect(task.timeline.some(event => event.kind === "created")).toBe(true);
});

it("allows an approved business action without a prewritten command, and audits each actual step", async () => {
  let task = await toPlan(await create());
  const actionPlan: RecoveryPlan = { ...plan, actions: [{ actionId: "fix", operationScope: { operation: "file.write", target: "/workspace/memory.md" }, summary: "修正本任务配置", risk: "局部修改", verification: "读取配置确认", rollback: "恢复原值" }] };
  await agent.report(task, { kind: "plan", phase: "plan", plan: actionPlan });
  task = await current(task.taskId);
  const operation = { clientRequestId: "actual-shell-1", purpose: "落实获批配置调整", actionId: "fix", command: "printf first-step" };
  await expect(agent.apply(task, operation)).rejects.toMatchObject({ status: 403 });
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  const frozen = (await current(task.taskId)).plan;
  vi.mocked(runtime.executeShell).mockResolvedValueOnce({ status: "failed", exitCode: 2, stdout: "", stderr: "invalid argument", durationMs: 1 });
  const first = await agent.apply(await current(task.taskId), operation);
  expect(first.status).toBe("failed");
  const second = await agent.apply(await current(task.taskId), { ...operation, clientRequestId: "actual-shell-2", command: "printf corrected-step" });
  expect(second.status).toBe("succeeded");
  const firstAudit = await agent.toolGet(await current(task.taskId), first.toolCallId);
  const secondAudit = await agent.toolGet(await current(task.taskId), second.toolCallId);
  expect(firstAudit.request).toMatchObject({ actionId: "fix", command: "printf first-step" });
  expect(secondAudit.request).toMatchObject({ actionId: "fix", command: "printf corrected-step" });
  expect((await current(task.taskId)).plan).toEqual(frozen);
  await expect(agent.apply(await current(task.taskId), { ...operation, actionId: "outside-plan", clientRequestId: "outside" })).rejects.toMatchObject({ status: 422 });
  await expect(agent.apply(await current(task.taskId), { ...operation, command: "printf changed-with-same-key" })).rejects.toThrow();
});
it("accepts actual Engine parameters independent of the saved execution reference", async () => {
  let task = await toPlan(await create());
  const reference = { method: "POST" as const, path: "/api/file/upload", upload: { targetPath: "/workspace/memory.md", content: "reference content" } };
  await agent.report(task, { kind: "plan", phase: "plan", plan: { ...plan, actions: [{ ...plan.actions[0], type: "engine_operation", operationScope: undefined, command: undefined, engineOperation: reference }] } });
  task = await current(task.taskId); await service.decide(task.taskId, task.plan!.digest, true, actor);
  vi.mocked(runtime.request).mockResolvedValueOnce({ status: 422, body: { success: false, message: "invalid fields" } })
    .mockResolvedValue({ status: 200, body: { success: true } });
  const first = await agent.apply(await current(task.taskId), { clientRequestId: "engine-first", purpose: "按现场组织配置调整", actionId: "fix", engineOperation: reference });
  expect(first.status).toBe("failed");
  const actual = { ...reference, upload: { ...reference.upload, filename: "memory.md", content: "current-field content" } };
  const second = await agent.apply(await current(task.taskId), { clientRequestId: "engine-next", purpose: "根据回执完善同一配置调整", actionId: "fix", engineOperation: actual });
  expect(second.status).toBe("succeeded");
  expect(runtime.request).toHaveBeenLastCalledWith(expect.objectContaining(actual));
  expect((await agent.toolGet(await current(task.taskId), second.toolCallId)).request).toMatchObject({ engineOperation: {
    ...actual, upload: { ...actual.upload, content: actual.upload.content },
  } });
  expect((await current(task.taskId)).plan!.actions[0].engineOperation).toEqual(reference);
  expect(await agent.apply(await current(task.taskId), { clientRequestId: "shell-fallback", purpose: "通过受控Shell落实同一文件调整", actionId: "fix", command: "printf repaired > /workspace/memory.md" })).toMatchObject({ status: "succeeded" });
});
it("lets an approved adjustment use an Engine request instead of the reference Shell", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId); await service.decide(task.taskId, task.plan!.digest, true, actor);
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true } });
  vi.mocked(runtime.executeShell).mockClear();
  const request = { method: "POST", path: "/api/file/upload", upload: { targetPath: "/workspace/memory.md", content: "adjusted" } };
  await agent.apply(await current(task.taskId), { clientRequestId: "api-instead", purpose: "通过接口落实同一配置调整", actionId: "fix", engineOperation: request });
  expect(runtime.request).toHaveBeenCalledOnce(); expect(runtime.executeShell).not.toHaveBeenCalled();
  await expect(agent.apply(await current(task.taskId), { clientRequestId: "both-transports", purpose: "无效的双重操作", actionId: "fix", command: "true", engineOperation: request })).rejects.toMatchObject({ status: 422 });
  await expect(agent.apply(await current(task.taskId), { clientRequestId: "invalid-engine", purpose: "无效的空接口请求", actionId: "fix", engineOperation: null })).rejects.toMatchObject({ status: 422 });
});
it("TE adjusts and verifies an approved cron action through actual API calls without Shell", async () => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" }; let task = await create("approval", { target: { ...request.target, engine: "TE" } });
  const { planProtocol } = await agent.bootstrap(task);
  expect(planProtocol.allowedOperations).toEqual(["file.write", "file.delete", "engine.restart", "cron.create", "cron.update", "cron.delete", "cron.run"]);
  expect(planProtocol.operationScope).toContain("启用和禁用使用 cron.update");
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: [] } });
  const inspected = await agent.inspect(task, { operation: "engine_read", request: { method: "GET", path: "/api/v1/bot/cron" }, clientRequestId: "read-cron", purpose: "核对诊断中的重复定时任务" });
  expect(inspected.status).toBe("succeeded");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "诊断与现场需要清理重复定时任务", evidence_ids: [inspected.evidenceId] });
  task = await current(task.taskId);
  const actionPlan: RecoveryPlan = { ...plan, actions: [{ actionId: "remove-cron", operationScope: { operation: "cron.delete", target: "duplicate-daily" }, summary: "删除已定位的重复日报任务", risk: "删除重复项，保留正常项", verification: "确认重复任务已不存在", rollback: "用原配置恢复" }] };
  await agent.report(task, { kind: "plan", phase: "plan", plan: actionPlan }); task = await current(task.taskId);
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  const operation = { clientRequestId: "remove", purpose: "删除方案指定的重复日报", actionId: "remove-cron", engineOperation: { method: "DELETE", path: "/api/v1/bot/cron/duplicate-daily" } };
  await agent.apply(await current(task.taskId), operation);
  expect(runtime.request).toHaveBeenLastCalledWith(expect.objectContaining(operation.engineOperation));
  const verified = await agent.inspect(await current(task.taskId), { operation: "engine_read", request: { method: "GET", path: "/api/v1/bot/cron" }, clientRequestId: "check-after", purpose: "验证指定重复任务已删除" });
  expect(verified.status).toBe("succeeded");
  expect(runtime.executeShell).not.toHaveBeenCalled();
  await expect(agent.apply(await current(task.taskId), { clientRequestId: "no-shell", purpose: "TE不能执行Shell", actionId: "remove-cron", command: "true" })).rejects.toMatchObject({ status: 422 });
  await expect(agent.apply(await current(task.taskId), { ...operation, clientRequestId: "undeclared", engineOperation: { method: "POST", path: "/api/v1/engine/restart" } })).rejects.toThrow();
  await expect(agent.apply(await current(task.taskId), { ...operation, clientRequestId: "with-headers", engineOperation: { ...operation.engineOperation, headers: {} } })).rejects.toMatchObject({ status: 422 });
});

it("old release cannot end a replacement execution after a version conflict", async () => {
  const task = await create();
  const update = tasks.update.bind(tasks);
  vi.spyOn(tasks, "update").mockImplementationOnce(async () => {
    await update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
      execution: { ...value.execution!, executionId: "replacement", jobId: "replacement-job" } }));
    return null;
  });
  await agent.release(task);
  expect((await current(task.taskId)).execution).toMatchObject({ executionId: "replacement", state: "dispatching", jobId: "replacement-job" });
});

it("old dispatch cannot attach its job to a replacement execution after a version conflict", async () => {
  let task = await create();
  await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    execution: { ...value.execution!, state: "ended" } }));
  task = await current(task.taskId);
  const update = tasks.update.bind(tasks);
  vi.spyOn(tasks, "update").mockImplementationOnce(update).mockImplementationOnce(async (id, version) => {
    await update(id, version, value => ({ ...value, version: value.version + 1,
      execution: { ...value.execution!, executionId: "replacement", jobId: "replacement-job" } }));
    return null;
  });
  await agent.start(task);
  expect((await current(task.taskId)).execution).toMatchObject({ executionId: "replacement", jobId: "replacement-job" });
  expect(ais.stopExecution).toHaveBeenCalledWith("job-2", "owner");
});

it.each([false, true])("the same tool key cannot identify changed advice, including legacy envelope fields: %s", async legacyEnvelope => {
  const task = await create();
  const body = { clientRequestId: "advice-content", purpose: "纠正调用后继续", message: "请核对参数并重试原操作",
    ...(legacyEnvelope ? { schemaVersion: "repair-tool-request/v1", runtimeTargetVersion: 1, payload: { unchanged: true } } : {}) };
  const sent = await agent.retryMessage(task, body);
  const call = await agent.toolGet(await current(task.taskId), sent.toolCallId);
  expect(call.request).toMatchObject({ message: body.message, requestDigest: expect.stringMatching(/^[a-f0-9]{64}$/) });
  await expect(agent.retryMessage(await current(task.taskId), { ...body, message: "请跳过原操作" })).rejects.toThrow(/idempotency conflict/);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});

it("the same tool key cannot identify changed uploaded content", async () => {
  let task = await create("automatic");
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "配置内容需要调整", evidence_ids: [read.evidenceId] });
  await agent.report(await current(task.taskId), { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId);
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true } });
  const body = { actionId: "fix", clientRequestId: "upload-content", purpose: "落实获批配置调整", engineOperation: { method: "POST", path: "/api/file/upload", upload: { targetPath: "/workspace/memory.md", content: '{"enabled":true}' } } };
  const result = await agent.apply(task, body);
  expect((await agent.toolGet(task, result.toolCallId)).request).toMatchObject({ engineOperation: { upload: { content: body.engineOperation.upload.content } } });
  await expect(agent.apply(await current(task.taskId), { ...body, engineOperation: { ...body.engineOperation, upload: { ...body.engineOperation.upload, content: '{"enabled":false}' } } })).rejects.toThrow(/idempotency conflict/);
  expect(runtime.request).toHaveBeenCalledTimes(1);
});

it("Debug records a targeted message and its decision without delivering it", async () => {
  let task = await create("debug");
  const read = await inspect(task);
  const body = { clientRequestId: "draft-message", purpose: "演练针对性消息", message: "请核对真实权限返回后继续原步骤" };
  const draft = await agent.retryMessage(await current(task.taskId), body);
  expect(draft.result).toMatchObject({ status: "not_intervened", dry_run: true, message: body.message });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "建议原 Bot 调整错误行为", evidence_ids: [read.evidenceId, draft.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", judgment: { effect: "not_intervened" } });
  expect(task.timeline.some(event => (event.payload.result as any)?.message === body.message)).toBe(true);
  expect(task.delivery).toBeUndefined(); expect(runtime.sendMessage).not.toHaveBeenCalled(); expect(notifier.notify).not.toHaveBeenCalled();
});

it("a failed pre-send session read does not poison later writes", async () => {
  let task = await create(); await inspect(task, "establish-session"); task = await current(task.taskId);
  vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("read unavailable"));
  const failed = await agent.retryMessage(task, { clientRequestId: "preflight-failed", purpose: "发送继续建议", message: "继续" });
  expect(failed.status).toBe("failed"); expect(failed.result).toMatchObject({ dispatched: false }); expect(runtime.sendMessage).not.toHaveBeenCalled();
  const retried = await agent.retryMessage(await current(task.taskId), { clientRequestId: "preflight-retried", purpose: "读取恢复后发送", message: "继续" });
  expect(retried.status).toBe("succeeded"); expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

it("unknown writes require fresh evidence and can then continue without rewriting audit", async () => {
  let task = await toPlan(await create("automatic")); await agent.report(task, { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId);
  vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("response lost"));
  const body = { actionId: "fix", clientRequestId: "lost-write", purpose: "执行获批动作" };
  const lost = await agent.apply(task, body); expect(lost.status).toBe("unknown");
  await expect(agent.apply(await current(task.taskId), { ...body, clientRequestId: "blind-retry" })).rejects.toMatchObject({ status: 409 });
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true } });
  const evidence = await agent.inspect(await current(task.taskId), { operation: "engine_read", request: { method: "GET", path: "/api/engine/status" }, clientRequestId: "verify-lost-write", purpose: "核实写入是否生效" });
  await agent.reconcileWrite(await current(task.taskId), { clientRequestId: "resolve-lost", purpose: "依据现场核实结果", sourceToolCallId: lost.toolCallId, outcome: "not_applied", summary: "现场仍是原值", evidenceToolCallIds: [evidence.toolCallId] });
  const next = await agent.apply(await current(task.taskId), { ...body, clientRequestId: "verified-retry", command: "printf corrected" });
  expect(next.status).toBe("succeeded"); expect((await agent.toolGet(await current(task.taskId), lost.toolCallId)).status).toBe("unknown");
  await expect(agent.reconcileWrite(await current(task.taskId), { clientRequestId: "opposite", purpose: "不能覆盖核实结果", sourceToolCallId: lost.toolCallId, outcome: "applied", summary: "相反结论", evidenceToolCallIds: [evidence.toolCallId] })).rejects.toMatchObject({ status: 409 });
});

it("approved cron deletion cannot become an MCP change or delete another cron", async () => {
  let task = await toPlan(await create("automatic"));
  const operation = { method: "DELETE" as const, path: "/api/cron/duplicate-daily" };
  await agent.report(task, { kind: "plan", phase: "plan", plan: { ...plan, actions: [{ ...plan.actions[0], type: "engine_operation", operationScope: undefined, command: undefined, engineOperation: operation }] } });
  task = await current(task.taskId); vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true } });
  const body = { actionId: "fix", clientRequestId: "delete-scoped", purpose: "删除指定重复任务" };
  await expect(agent.apply(task, { ...body, engineOperation: { method: "PUT", path: "/api/mcp/server" } })).rejects.toMatchObject({ status: 403 });
  await expect(agent.apply(task, { ...body, engineOperation: { method: "DELETE", path: "/api/cron/other" } })).rejects.toMatchObject({ status: 403 });
  await agent.apply(task, { ...body, engineOperation: { ...operation, body: { confirmed: true } } });
  expect(runtime.request).toHaveBeenCalledOnce();
});

it("tool history stays in the audit ledger while Task config stays bounded", async () => {
  const task = await create(); vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { content: "evidence ".repeat(2000) } });
  const before = (await repo.findTask(task.taskId))!.config_json.length;
  for (let index = 0; index < 12; index++) await agent.inspect(await current(task.taskId), { operation: "engine_read", request: { method: "GET", path: "/api/engine/status" }, clientRequestId: `history-${index}`, purpose: "读取运行状态证据" });
  const raw = (await repo.findTask(task.taskId))!.config_json, state = JSON.parse(raw);
  expect(raw.length).toBe(before); expect(state.timeline).toBeUndefined(); expect(state.evidence).toBeUndefined(); expect(state.pending_results).toBeUndefined();
  expect((await current(task.taskId)).evidence).toHaveLength(12);
  expect((await current(task.taskId)).timeline.filter(event => event.kind === "operation")).toHaveLength(12);
  expect(await db.query("SELECT name FROM sqlite_master WHERE name IN ('ce_session_recovery_records', 'ce_session_recovery_results')")).toEqual([]);
});

it("migrates legacy pending results before reporting and preserves their original version", async () => {
  const task = await create();
  const result = { event_id: task.request.event_id, task_id: task.taskId, result_version: task.version, status: "failed" as const, phase: task.phase, waiting_reason: null, effect: "unknown" as const, result_text: "历史结果", evidence_ids: [] };
  const evidence = { evidence_id: "legacy-evidence", tool_call_id: "legacy-call", source: "session", location: "original:1", excerpt: "历史现场" };
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify({ ...task, status: "failed", evidence: [evidence], pending_results: [{ ...result, status: "waiting_owner", result_version: 0 }, result] }), task.taskId]);
  const legacyRow = (await repo.findTask(task.taskId))!;
  expect((await tasks.listForReporting(20, "", "prod")).tasks).toEqual([]);
  expect(await repo.findTask(task.taskId)).toEqual(legacyRow);
  // Old tasks had no phase artifact; preserve the original Step identity.
  await db.exec("UPDATE ce_steps SET output_json = NULL WHERE task_id = ? AND step_no > 0", [task.taskId]);
  await service.reportResults();
  expect(callbacks).toEqual([result]); expect((await current(task.taskId)).pending_results).toEqual([]);
  expect((await current(task.taskId)).evidence).toEqual([evidence]);
  expect(JSON.parse((await repo.findTask(task.taskId))!.config_json).artifactStorage).toBe(2);
});
it("waiting snapshots survive same-task resumes and restart before the final result is delivered", async () => {
  let task = await toObserve(await create());
  const firstExecution = task.execution!.executionId;
  now = task.observationDeadlineAt! + 1;
  await service.maintenance(); task = await current(task.taskId);
  expect(task.status).toBe("waiting_effect");
  const firstResult = structuredClone(task.pending_results[0]);
  vi.spyOn(reporter, "report").mockRejectedValueOnce(new Error("receiver unavailable"));
  await service.reportResults();
  const failed = JSON.parse((await repo.findTask(task.taskId))!.config_json).resultDelivery[0];
  expect(failed).toMatchObject({ resultVersion: firstResult.result_version, attempts: 1 });
  expect(failed.retryAfterMs).toBeGreaterThan(Date.now());
  await service.resume(task.taskId); task = await current(task.taskId);
  expect(task.execution!.executionId).not.toBe(firstExecution);
  expect(task.phase).toBe("observe"); expect(task.requiresFreshObservation).toBe(true);
  await inspect(task, "resume-read-1"); task = await current(task.taskId);
  expect(task.requiresFreshObservation).toBe(false);
  now = task.observationDeadlineAt! + 1;
  await service.maintenance(); task = await current(task.taskId);
  expect(task.pending_results).toHaveLength(2);
  await service.resume(task.taskId); task = await current(task.taskId);
  const read = await inspect(task, "resume-read-2");
  await agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment: {
    delivery: "confirmed", response: "acted", effect: "recovered", summary: "原操作成功", evidence_ids: [read.evidenceId] } });
  task = await current(task.taskId);
  expect(task.pending_results.map(result => result.status)).toEqual(["waiting_effect", "waiting_effect", "completed"]);
  expect(task.pending_results[0]).toEqual(firstResult);
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
  expect((await tasks.listForReporting(20, "", "pre")).tasks).toEqual([]);
  const stored = JSON.parse((await repo.findTask(task.taskId))!.config_json);
  expect(stored.pending_results).toBeUndefined(); expect(stored.timeline).toBeUndefined();
  expect(stored.resultDelivery).toHaveLength(3);
  expect(stored.resultDelivery[0]).not.toHaveProperty("result_text");
  const clock = vi.spyOn(Date, "now").mockImplementation(() => failed.retryAfterMs + 1);
  try {
    const restoredTasks = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), artifactStore);
    const restarted = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks: restoredTasks, agent, notifier, reporter, observationMs: 3600000 });
    const versions = task.pending_results.map(result => result.result_version);
    await restarted.reportResults();
    expect(callbacks.map(result => result.result_version)).toEqual(versions);
    expect((await current(task.taskId)).pending_results).toEqual([]);
    await restarted.reportResults(); expect(callbacks).toHaveLength(3);
    await tasks.acknowledgeResult(task.taskId, versions[0], 1);
    const acknowledgments = (await current(task.taskId)).timeline.filter(event => event.kind === "result");
    expect(acknowledgments).toHaveLength(3);
    expect(acknowledgments.map(event => event.payload.attempts)).toEqual([2, 1, 1]);
    expect(await db.query("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'")).toHaveLength(1);
  } finally { clock.mockRestore(); }
});

it("a late callback acknowledgment preserves a resumed execution and a newer final result", async () => {
  let task = await toObserve(await create());
  now = task.observationDeadlineAt! + 1;
  await service.maintenance(); task = await current(task.taskId);
  const waitingVersion = task.pending_results[0].result_version;
  let accepted!: () => void;
  const receiver = new Promise<void>(resolve => { accepted = resolve; });
  vi.spyOn(reporter, "report").mockImplementationOnce(async result => { callbacks.push(result); await receiver; });
  const reporting = service.reportResults();
  await vi.waitFor(() => expect(callbacks).toHaveLength(1));
  try {
    await service.resume(task.taskId); task = await current(task.taskId);
    const execution = task.execution!.executionId;
    const read = await inspect(task, "read-during-callback");
    await agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment: {
      delivery: "confirmed", response: "acted", effect: "recovered", summary: "已恢复", evidence_ids: [read.evidenceId] } });
    accepted(); await reporting;
    task = await current(task.taskId);
    expect(task.status).toBe("completed"); expect(task.execution!.executionId).toBe(execution);
    expect(task.pending_results).toHaveLength(1); expect(task.pending_results[0].status).toBe("completed");
    expect(task.timeline.filter(event => event.kind === "result").map(event => event.payload.result_version)).toEqual([waitingVersion]);
    await service.reportResults(); expect(callbacks.map(result => result.status)).toEqual(["waiting_effect", "completed"]);
  } finally { accepted(); await reporting; }
});

it("a failed Step write cannot commit terminal state or its pending callback", async () => {
  const task = await create();
  const before = (await repo.findTask(task.taskId))!;
  await db.exec("CREATE TRIGGER fail_recovery_report BEFORE UPDATE OF output_json ON ce_steps BEGIN SELECT RAISE(ABORT, 'report unavailable'); END");
  await expect(service.executionFailure(task.taskId, "执行异常")).rejects.toThrow("report unavailable");
  const unchanged = (await repo.findTask(task.taskId))!;
  expect(unchanged.config_json).toBe(before.config_json); expect(unchanged.status).toBe(before.status);
  await service.reportResults(); expect(callbacks).toEqual([]);
  await db.exec("DROP TRIGGER fail_recovery_report");
  await service.executionFailure(task.taskId, "执行异常");
  await service.reportResults(); expect(callbacks).toHaveLength(1); expect(callbacks[0].status).toBe("failed");
});

it("isolates background recovery maintenance by control plane without hiding shared tasks", async () => {
  let task = await create();
  expect(task.controlPlaneEnvironment).toBe("pre");
  const prodAgent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "prod", tasks, repo,
    audit: new AgentToolCallRepository(db, "session_recovery"), ais, runtime, targets: { resolve: async () => target },
    business: () => prod, publicBaseUrl: "https://clawweb.alipay.com", snapshotId: 1, nasRoots: [directory],
    leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now });
  const prod = new SessionRecoveryTaskService({ controlPlaneEnvironment: "prod", tasks, agent: prodAgent, notifier, reporter, observationMs: 3600000, now: () => now });
  expect((await prod.detail(task.taskId, actor)).taskId).toBe(task.taskId);
  now = task.execution!.leaseExpiresAt * 1000 + 1;
  await prod.maintenance(); await prodAgent.start(task);
  await reconcileAgentExecutions({ controlPlaneEnvironment: "prod", db, repo, handlers: { session_recovery: prodAgent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  const step = (await repo.findStep(task.aisExecution!.stepId))!;
  await prodAgent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step, report: { status: "failed", retryable: true } });
  await prod.executionFailure(task.taskId, "foreign worker", task.execution!.executionId, true);
  expect(ais.getJobStatusDetail).not.toHaveBeenCalled(); expect(ais.stopExecution).not.toHaveBeenCalled();
  expect(submissions).toHaveLength(1); expect((await current(task.taskId)).execution).toEqual(task.execution);
  await service.executionFailure(task.taskId, "finished", task.execution!.executionId);
  await prod.reportResults(); expect(callbacks).toEqual([]);
  await service.reportResults(); expect(callbacks).toHaveLength(1);
});

it("does not release a replacement execution carrying an older retry marker", async () => {
  let task = await create(); const failedExecution = task.execution!.executionId;
  const step = (await repo.findStep(task.aisExecution!.stepId))!;
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step, report: { status: "failed", retryable: true } });
  task = await current(task.taskId);
  expect(task.executionRetry?.executionId).toBe(failedExecution);
  await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    execution: { ...value.execution!, executionId: "replacement-from-legacy-worker", state: "dispatching", invalidatedAt: null, jobId: "replacement-job" } }));
  now += 20000;
  await service.maintenance();
  task = await current(task.taskId);
  expect(task.execution!.executionId).toBe("replacement-from-legacy-worker");
  expect(task.execution!.state).toBe("dispatching"); expect(submissions).toHaveLength(1);
});

it("leaves unmarked legacy recovery tasks unclaimed rather than guessing from the Bot environment", async () => {
  let task = await create();
  await tasks.update(task.taskId, task.version, value => { const { controlPlaneEnvironment: _environment, ...legacy } = value; return { ...legacy, version: value.version + 1 }; });
  task = await current(task.taskId); now = task.execution!.leaseExpiresAt * 1000 + 1;
  await service.maintenance();
  await reconcileAgentExecutions({ controlPlaneEnvironment: "pre", db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) });
  expect(ais.getJobStatusDetail).not.toHaveBeenCalled(); expect(ais.stopExecution).not.toHaveBeenCalled();
  expect((await current(task.taskId)).execution).toEqual(task.execution);
});
it.each([true, false])("accepts AIVision delivery from the current actual receipt, initial=%s", async initial => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" };
  let task = await create("automatic", { target: { ...request.target, engine: "TE" } });
  const read = vi.fn().mockResolvedValue({ traces: [] });
  (agent as any).deps.trajectory = { read };
  task = (await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1, timeline: [...value.timeline, { event_id: "legacy-session", kind: "operation", at_ms: now, actor: "agent", summary: "旧会话读取", payload: { result: { session_id: request.session_key } } }] })))!;
  expect((await agent.bootstrap(task)).tools).toMatchObject({ session_read: false, trajectory_read: true });
  await expect(agent.sessionRead(task, { clientRequestId: "te-old-reader", purpose: "旧会话工具调用" })).rejects.toMatchObject({ status: 422, code: "unsupported_session_read" });
  if (initial) task = await toObserve(task);
  else {
    const basis = await agent.trajectoryRead(task, { clientRequestId: "te-assess", purpose: "读取原任务的环境阻塞证据" });
    await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "需要先解决环境阻塞", evidence_ids: [basis.evidenceId] });
    task = await current(task.taskId);
    await agent.report(task, { kind: "plan", phase: "plan", plan: { ...plan, actions: [], owner_cooperation: [] } });
    now += 1000;
    await agent.retryMessage(await current(task.taskId), { clientRequestId: "after-fix", purpose: "环境处理后继续原任务", message: "请继续原任务" });
    await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: true, success: true, summary: "环境已核实，已发送继续指引" });
    task = await current(task.taskId); expect(task.delivery).toBeUndefined();
  }
  expect(runtime.request).not.toHaveBeenCalled(); expect(runtime.executeShell).not.toHaveBeenCalled();
  const sent = [...task.timeline].reverse().find(event => typeof (event.payload.result as any)?.initial_message === "boolean")!;
  const receipt = (await agent.toolGet(task, String(sent.payload.tool_call_id))).result as any;
  read.mockResolvedValue({ delivery_confirmed: true, delivery: { location: "trace.input", marker: "verified" }, traces: [] });
  const evidence = await agent.trajectoryRead(task, { clientRequestId: "te-observe", purpose: "从真实用户轨迹核实触达" });
  expect(read).toHaveBeenCalledWith(expect.objectContaining({ messageMarker: `[会话自愈：${receipt.delivery_key}]\n`, notBeforeMs: receipt.started_at_ms }));
  await agent.report(await current(task.taskId), { kind: "observation", phase: initial ? "observe" : "verify", judgment: {
    delivery: "confirmed", response: "acted", effect: "recovered", summary: "完整轨迹确认执行恢复", evidence_ids: [evidence.evidenceId] } });
  expect((await current(task.taskId)).status).toBe("completed");
});

it("returns actionable 422 feedback for missing delivery and shortened evidence without changing the phase", async () => {
  let task = await create();
  const read = await inspect(task); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "assessment", phase: "assess", decision: "message", summary: "无法投递", evidence_ids: [read.evidenceId] }))
    .rejects.toMatchObject({ status: 422, message: expect.stringContaining("blocked") });
  await expect(agent.report(task, { kind: "assessment", phase: "assess", decision: "observe", summary: "保留调查结论", evidence_ids: ["evidence-tc-short"] }))
    .rejects.toMatchObject({ status: 422, message: expect.stringContaining("完整 evidenceId") });
  await expect(agent.report(task, { kind: "blocked", phase: "assess", reason: "session_unavailable", summary: "原会话不可投递" }))
    .rejects.toMatchObject({ status: 422, message: expect.stringContaining("省略 reason") });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "assess" });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
  await agent.report(task, { kind: "blocked", phase: "assess", summary: "原会话不可投递，当前无法继续" });
  expect(await current(task.taskId)).toMatchObject({ status: "failed", judgment: { effect: "unknown" } });
});
