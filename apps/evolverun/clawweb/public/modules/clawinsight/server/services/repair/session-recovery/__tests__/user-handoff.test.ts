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

it("Agent can observe existing progress without sending, and an unknown send is never replayed", async () => {
  let task = await create("automatic", { tc_fault_label: null }); const read = await inspect(task, "read-progress");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "observe", summary: "原任务正在推进，先观察", evidence_ids: [read.evidenceId] });
  expect((await current(task.taskId)).observationDeadlineAt).toBe(now + 3600000); expect(runtime.sendMessage).not.toHaveBeenCalled();
  task = await create("approval", { event_id: "second-event", delivery_key: "second-initial" });
  vi.mocked(runtime.sendMessage).mockRejectedValueOnce(new Error("transport outcome unknown"));
  const body = { clientRequestId: "unknown-advice", purpose: "纠正操作后继续", message: "请按真实返回修正原操作" };
  const send = await agent.retryMessage(task, body); expect(send.status).toBe("unknown");
  expect((await current(task.taskId)).delivery!.delivery.status).toBe("unknown");
  await agent.retryMessage(await current(task.taskId), body); expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  await expect(agent.retryMessage(await current(task.taskId), { ...body, clientRequestId: "blind-repeat" })).rejects.toMatchObject({ status: 409 });
});
it("historical intervention cannot confirm this delivery but existing recovery can complete without a new message", async () => {
  let task = await create();
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "old-advice", message: { role: "user", content: "[会话自愈：old-delivery]\n继续" } }) + "\n");
  const read = await inspect(task, "historical-read");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "observe", summary: "原任务此前已恢复", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  const judgment = { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原任务已恢复，本次未发消息", evidence_ids: [read.evidenceId] };
  await expect(agent.report(task, { kind: "observation", phase: "observe", judgment })).rejects.toMatchObject({ status: 422 });
  await agent.report(task, { kind: "observation", phase: "observe", judgment: { ...judgment, delivery: "unconfirmed", response: "none" } });
  expect((await current(task.taskId)).status).toBe("completed");
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it.each(["acted", "replied"] as const)("rejects %s attribution without confirmed guidance, then accepts independent recovery", async response => {
  let task = await create();
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "observe", summary: "Bot 已自行恢复，无需发送消息", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  const judgment = { delivery: "unconfirmed", response, effect: "recovered", summary: "Bot 已自行完成原任务", evidence_ids: [read.evidenceId] };
  await expect(agent.report(task, { kind: "observation", phase: "observe", judgment })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "observe", version: task.version });
  await agent.report(task, { kind: "observation", phase: "observe", judgment: { ...judgment, response: "none" } });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", interventionStatus: "none", judgment: { delivery: "unconfirmed", response: "none", effect: "recovered" } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it.each(["acted", "replied"] as const)("accepts %s attribution with controlled evidence of current guidance delivery", async response => {
  let task = await toObserve(await create());
  const read = await inspect(task); task = await current(task.taskId);
  await agent.report(task, { kind: "observation", phase: "observe", judgment: { delivery: "confirmed", response, effect: "recovered", summary: "Bot 收到本次指引后继续原任务", evidence_ids: [read.evidenceId] } });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", judgment: { delivery: "confirmed", response, effect: "recovered" } });
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

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


it.each(["assess", "observe", "plan", "verify"] as const)("notifies the Owner and ends a handbook-directed handoff in %s without a plan or continuation", async phase => {
  let task = phase === "observe" ? await toObserve(await create()) : await create();
  if (["plan", "verify"].includes(phase)) task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase, version: value.version + 1 })))!;
  const read = await inspect(task, "handoff-evidence"); task = await current(task.taskId);
  const summary = "目标业务资源已停用，手册明确要求 Owner 在管理页面恢复资源后自行继续原任务。";
  await agent.report(task, { kind: "owner_handoff", phase, effect: "not_recovered", summary, evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", phase, completionReason: "owner_handoff", plan: null,
    ownerDeadlineAt: null, observationDeadlineAt: null, execution: { state: "ended" }, judgment: { effect: "not_recovered", summary } });
  expect(notifier.notify).toHaveBeenCalledExactlyOnceWith({ ownerId: "owner", taskId: task.taskId, kind: "result", effect: "not_recovered", summary });
  expect(task.timeline.some(event => event.kind === "notification" && event.payload.kind === "result" && event.payload.sent === true)).toBe(true);
  expect(task.timeline.some(event => event.kind === "plan" || event.kind === "owner_decision")).toBe(false);
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "completed", completion_reason: "owner_handoff", effect: "not_recovered", intervention_status: phase === "observe" ? "performed" : "none" });
  await service.executionFailure(task.taskId, "late AIS callback", task.execution!.executionId, true);
  await service.maintenance();
  expect(submissions).toHaveLength(1);
  await expect(agent.report(task, { kind: "owner_handoff", phase, summary, evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(notifier.notify).toHaveBeenCalledTimes(1);
});

it.each(["assess", "observe", "plan", "verify"].flatMap(phase => ["recovered", "not_recovered", "unknown"].map(effect => ({ phase, effect }))))(
  "keeps the current $effect judgment when handing off from $phase without waiting or repeating work", async ({ phase, effect }) => {
    let task = await create();
    await appendFile(sessionFile, JSON.stringify({ type: "message", id: "task-progress", message: { role: "assistant", content: "已修正参数，子任务已完成并汇总。" } }) + "\n");
    const read = await inspect(task, "current-effect"); task = await current(task.taskId);
    task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase: phase as RecoveryStoredTask["phase"], version: value.version + 1,
      judgment: { delivery: "unconfirmed", response: "none", effect: "not_recovered", summary: "旧判断待复核", evidence_ids: [read.evidenceId] } })))!;
    const summary = "已核对本轮执行；Skill 的参数示例可由 Owner 后续优化，不需要重跑当前任务。";
    await agent.report(task, { kind: "owner_handoff", phase, effect, summary, evidence_ids: [read.evidenceId] });
    task = await current(task.taskId);
    expect(task).toMatchObject({ status: "completed", completionReason: "owner_handoff", phase, plan: null,
      ownerDeadlineAt: null, observationDeadlineAt: null, execution: { state: "ended" },
      judgment: { effect, summary, delivery: "unconfirmed", response: "none", evidence_ids: [read.evidenceId] } });
    expect(notifier.notify).toHaveBeenCalledExactlyOnceWith({ ownerId: "owner", taskId: task.taskId, kind: "result", effect, summary });
    expect(runtime.sendMessage).not.toHaveBeenCalled();
    expect(task.timeline.some(event => event.kind === "plan" || event.kind === "owner_decision")).toBe(false);
    await service.reportResults();
    expect(callbacks.at(-1)).toMatchObject({ status: "completed", completion_reason: "owner_handoff", effect, intervention_status: "none" });
    await service.maintenance(); expect(submissions).toHaveLength(1);
  });

it.each([undefined, "recovered", "not_recovered", "unknown"] as const)("uses saved effect %s for an older handoff, or unknown when no judgment exists", async effect => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  if (effect) task = (await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    judgment: { delivery: "unconfirmed", response: "none", effect, summary: "已有现场判断", evidence_ids: [read.evidenceId] } })))!;
  await agent.report(task, { kind: "owner_handoff", phase: "assess", summary: "请 Owner 查阅后续处理建议。", evidence_ids: [read.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", judgment: { effect: effect ?? "unknown" } });
});

it.each(["not_intervened", "invalid", null, 1])("rejects invalid handoff effect %s without ending or notifying", async effect => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "owner_handoff", phase: "assess", effect, summary: "有现场读取但效果值无效", evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", version: task.version });
  expect(notifier.notify).not.toHaveBeenCalled();
});

it.each(["disabled", "failed"])("ends without claiming Owner notification when the channel is %s", async state => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  if (state === "disabled") notifier.notify.mockResolvedValue(false);
  else notifier.notify.mockRejectedValue(new Error("notification unavailable"));
  await agent.report(task, { kind: "owner_handoff", phase: "assess", summary: "已核实 MCP 服务权限不足，请 Owner 在能力集中申请权限。", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", completionReason: "owner_handoff", execution: { state: "ended" } });
  expect(task.timeline.some(event => event.kind === "notification" && event.payload.sent === true)).toBe(false);
  if (state === "failed") expect(task.timeline.some(event => event.kind === "notification" && event.payload.sent === false)).toBe(true);
  await service.maintenance(); expect(submissions).toHaveLength(1);
});

it("requires saved scene evidence and never notifies from Debug or an in-progress apply", async () => {
  let task = await create();
  await expect(agent.report(task, { kind: "owner_handoff", phase: "assess", summary: "仅有诊断推测", evidence_ids: [] })).rejects.toMatchObject({ status: 422 });
  const read = await inspect(task); task = await current(task.taskId);
  task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase: "apply", version: value.version + 1 })))!;
  await expect(agent.report(task, { kind: "owner_handoff", phase: "apply", summary: "不得中途转交", evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 409 });
  const debug = await create("debug", { event_id: "debug-owner-notice", delivery_key: "debug-owner-notice" });
  const evidence = await inspect(debug);
  await expect(agent.report(await current(debug.taskId), { kind: "owner_handoff", phase: "assess", summary: "Debug不得发通知", evidence_ids: [evidence.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(notifier.notify).not.toHaveBeenCalled();
});


it("accepts an older package's permission report through the generic Owner handoff", async () => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  await agent.report(task, { kind: "blocked", phase: "assess", reason: "mcp_permission_required", summary: "服务明确要求当前身份授权，请 Owner 申请后继续。", evidence_ids: [read.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", completionReason: "owner_handoff", judgment: { effect: "not_recovered" } });
  expect(notifier.notify).toHaveBeenCalledTimes(1);
});

it("rejects a stale Owner handoff without notifying or ending the replacement execution", async () => {
  const stale = await create(); const read = await inspect(stale);
  await tasks.update(stale.taskId, (await current(stale.taskId)).version, value => ({ ...value, version: value.version + 1,
    execution: { ...value.execution!, executionId: "replacement-execution" } }));
  await expect(agent.report(stale, { kind: "owner_handoff", phase: "assess", summary: "已核实需 Owner 处理", evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(await current(stale.taskId)).toMatchObject({ status: "running", execution: { executionId: "replacement-execution" } });
  expect(notifier.notify).not.toHaveBeenCalled();
});

it("still advances ordinary unrecovered message intervention into a complete plan", async () => {
  const task = await toPlan(await create());
  expect(task).toMatchObject({ status: "running", phase: "plan", judgment: { effect: "not_recovered" } });
  expect(notifier.notify).not.toHaveBeenCalled();
  await agent.report(task, { kind: "plan", phase: "plan", plan });
  expect(await current(task.taskId)).toMatchObject({ status: "waiting_owner", phase: "apply" });
});

it.each(["accepted", "unknown"] as const)("requires phase results after %s message delivery instead of ordinary blocked", async receiptStatus => {
  const task = await create();
  if (receiptStatus === "unknown") {
    const send = vi.mocked(runtime.sendMessage).getMockImplementation()!;
    vi.mocked(runtime.sendMessage).mockImplementationOnce(async input => { await send(input); throw new Error("receipt lost"); });
  }
  const sent = await agent.retryMessage(task, { clientRequestId: "retry-failed-operation", purpose: "重试原失败步骤", message: "请重试原工具调用并如实报告" });
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "retry-result", message: { role: "toolResult", content: "原工具调用重试仍失败" } }) + "\n");
  const read = await inspect(await current(task.taskId), "retry-effect");
  const assessed = await current(task.taskId);
  expect(assessed.delivery?.delivery.status).toBe(receiptStatus);
  const blocked = { kind: "blocked", phase: "assess", summary: "已发出指引并重试，原故障仍存在，没有可直接执行的修复动作", evidence_ids: [read.evidenceId] };
  await expect(agent.report(assessed, blocked)).rejects.toMatchObject({ status: 422, message: expect.stringContaining("assessment") });
  expect(await current(task.taskId)).toMatchObject({ version: assessed.version, status: "running", phase: "assess", judgment: null, execution: assessed.execution });
  expect(actionNotifier.after).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "result" }));
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "已留存消息投递，进入效果回收", evidence_ids: [sent.evidenceId] });
  await expect(agent.report(await current(task.taskId), { ...blocked, phase: "observe" })).rejects.toMatchObject({ status: 422, message: expect.stringContaining("observation") });
  await agent.report(await current(task.taskId), { kind: "observation", phase: "observe", judgment: { delivery: "confirmed", response: "acted", effect: "not_recovered", summary: "实际重试仍失败", evidence_ids: [read.evidenceId] } });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "plan", judgment: { effect: "not_recovered" } });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(notifier.notify).not.toHaveBeenCalled();
});

it.each(["recovered", "not_recovered", "unknown"] as const)("requires an observation result for verification effect %s", async effect => {
  let task = await toPlan(await create("automatic"));
  await agent.report(task, { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId);
  await service.finishApply(task.taskId, task.version, { applicable: true, success: true, summary: "方案执行结束" });
  const read = await inspect(await current(task.taskId), "verify-effect");
  task = await current(task.taskId);
  await expect(agent.report(task, { kind: "blocked", phase: "verify", summary: "原任务执行结果" })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "verify", version: task.version });
  await agent.report(task, { kind: "observation", phase: "verify", judgment: { delivery: "confirmed", response: "acted", effect, summary: "依据实际执行回报效果", evidence_ids: [read.evidenceId] } });
  expect(await current(task.taskId)).toMatchObject({ phase: "verify", status: effect === "recovered" ? "completed" : effect === "not_recovered" ? "failed" : "running", judgment: { effect } });
});

it("keeps ordinary blocked for a proven failed delivery and an unrepairable plan", async () => {
  const beforeDelivery = await create();
  vi.mocked(runtime.sendMessage).mockResolvedValueOnce({ status: "failed", error: "delivery rejected" });
  await agent.retryMessage(beforeDelivery, { clientRequestId: "rejected-delivery", purpose: "发送具体指引", message: "继续原失败操作" });
  await agent.report(await current(beforeDelivery.taskId), { kind: "blocked", phase: "assess", summary: "投递被明确拒绝，无法继续干预" });
  expect(await current(beforeDelivery.taskId)).toMatchObject({ status: "failed", phase: "assess" });
  const planning = await toPlan(await create("approval", { event_id: "unrepairable-plan", delivery_key: "unrepairable-plan" }));
  await agent.report(planning, { kind: "blocked", phase: "plan", summary: "已独立调查，当前授权和能力下没有可行修复，保留具体依据" });
  expect(await current(planning.taskId)).toMatchObject({ status: "failed", phase: "plan" });
});
