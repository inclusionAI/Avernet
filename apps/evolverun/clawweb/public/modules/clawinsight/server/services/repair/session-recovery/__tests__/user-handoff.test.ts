import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, appendFile, readFile, realpath, rm } from "node:fs/promises";
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
import { SessionRecoveryNotifier } from "../notification.js";
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
it.each([
  ["permission", false, false, false], ["permission", false, true, false], ["permission", true, false, false], ["permission", true, true, false],
  ["permission", false, false, true], ["permission", false, true, true], ["permission", true, false, true], ["permission", true, true, true],
  ["configuration", false, false, true], ["configuration", true, true, false],
] as const)("extends %s feedback into one fresh continuation (initial delivery: %s, NAS: %s, restart: %s)", async (reason, hadInitial, useNas, restart) => {
  const first = hadInitial ? await toObserve(await create()) : await create();
  expect(await agent.bootstrap(first)).toMatchObject({ ownerCooperationProtocol: "session-recovery-owner/v1",
    ownerCooperationReasons: ["permission", "configuration"] });
  const oldDelivery = first.delivery;
  const evidence = await inspect(first);
  const summary = reason === "configuration" ? "审批路由缺接收目标，本轮跳过分发" : "服务权限需Owner申请";
  const instructions = reason === "configuration" ? "请在管理平台补齐当前审批路由；回读路由并查分发状态" : "请申请服务权限后点击已处理";
  await agent.report(await current(first.taskId), { phase: first.phase, kind: "owner_handoff", summary, evidence_ids: [evidence.evidenceId], owner_request: { reason, instructions } });
  let saved = await current(first.taskId);
  expect(saved.status).toBe("waiting_owner"); expect(saved.pending_results).toEqual([]);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(hadInitial ? 1 : 0);
  const requestId = saved.ownerRequest!.requestId;
  if (restart) await agent.report(saved, { phase: saved.phase, kind: "release" });
  await Promise.all([service.confirmOwner(saved.taskId, requestId, actor), service.confirmOwner(saved.taskId, requestId, actor)]);
  await service.confirmOwner(saved.taskId, requestId, actor);
  await vi.waitFor(async () => expect((await current(saved.taskId)).executionNo).toBe(restart ? 2 : 1));
  saved = await current(saved.taskId);
  expect(saved).toMatchObject({ phase: "assess", requiresFreshObservation: true, ownerRequest: { reason, status: "confirmed" } });
  if (reason === "configuration" && !hadInitial) expect(saved.interventionStatus).toBe("none");
  await expect(agent.report(saved, { phase: "assess", kind: "owner_handoff", summary: "仅根据Owner反馈宣称恢复", effect: "recovered", evidence_ids: [evidence.evidenceId] })).rejects.toMatchObject({ status: 409 });
  await expect(agent.report(saved, { phase: "assess", kind: "blocked", reason: "user_continued", summary: "仅根据旧记录宣称用户续聊", evidence_ids: [evidence.evidenceId] })).rejects.toMatchObject({ status: 409 });
  const body = { clientRequestId: "continue-1", purpose: "Owner 反馈后的续接", message: reason === "configuration"
    ? "请核验路由和分发状态，仅继续未分发的步骤。" : "Owner已处理权限，请重新核验失败的读取后继续原任务。" };
  // Old calls are either replayed in the same execution or rejected across executions.
  if (restart) await expect(inspect(saved)).rejects.toMatchObject({ name: "AgentToolCallIdempotencyConflictError" });
  else expect((await inspect(saved)).toolCallId).toBe(evidence.toolCallId);
  expect((await current(saved.taskId)).requiresFreshObservation).toBe(true);
  await expect(agent.retryMessage(saved, body)).rejects.toMatchObject({ status: 409 });
  const readBody = { purpose: "读取反馈后的最新原会话", from_line: 1, ...(useNas ? { nas_evidence: {
    source: `${directory}/prod/prod_staff_owner_openclaw_default/.openclaw/agents/main/sessions/original.jsonl`,
    session_id: "original", delivery_key: request.delivery_key,
    messages: (await readFile(sessionFile, "utf8")).trim().split("\n").map((line, index) => ({ line_no: index + 1, record: JSON.parse(line) })),
  } } : {}) };
  if (!useNas) vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("session read unavailable"));
  expect(await agent.sessionRead(saved, { ...readBody, clientRequestId: "failed-fresh-read",
    ...(useNas ? { nas_evidence: { ...readBody.nas_evidence!, session_id: "another-session" } } : {}) })).toMatchObject({ status: "failed" });
  saved = await current(saved.taskId);
  expect(saved.requiresFreshObservation).toBe(true);
  await expect(agent.retryMessage(saved, body)).rejects.toMatchObject({ status: 409 });
  vi.mocked(runtime.executeShell).mockClear();
  const fresh = await agent.sessionRead(saved, { ...readBody, clientRequestId: "fresh-after-feedback" });
  expect(fresh).toMatchObject({ status: "succeeded", result: { source: useNas ? readBody.nas_evidence!.source : await realpath(sessionFile),
    messages: expect.arrayContaining([expect.objectContaining({ record: expect.objectContaining({ id: "failed-operation" }) })]) } });
  if (useNas) expect(runtime.executeShell).not.toHaveBeenCalled();
  expect((await current(saved.taskId)).requiresFreshObservation).toBe(false);
  const resumed = await agent.retryMessage(await current(saved.taskId), body);
  await agent.retryMessage(await current(saved.taskId), { ...body, clientRequestId: "different-agent-id" });
  saved = await current(saved.taskId);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(hadInitial ? 2 : 1);
  expect(saved.delivery).toEqual(oldDelivery);
  expect(resumed.result).toMatchObject({ initial_message: false, owner_request_id: requestId });
  expect((resumed.result as Record<string, unknown>).delivery_key).not.toBe(request.delivery_key);
  await agent.report(saved, { kind: "assessment", phase: "assess", decision: "message", summary: "已按反馈续接，继续观察实际执行", evidence_ids: [resumed.evidenceId] });
  expect((await current(saved.taskId)).phase).toBe("observe");
  expect((await current(saved.taskId)).timeline.filter(event => event.kind === "owner_decision" && event.payload.owner_request_id === requestId)).toHaveLength(1);
});

it("observes an already accepted recovery message before opening a configuration request", async () => {
  const task = await create();
  const read = await inspect(task);
  const sent = await agent.retryMessage(await current(task.taskId), { clientRequestId: "initial-advice",
    purpose: "核对当前分发配置", message: "请核对分发目标后继续原步骤。" });
  const handoff = { kind: "owner_handoff", summary: "分发目标尚未配置", evidence_ids: [read.evidenceId],
    owner_request: { reason: "configuration", instructions: "请补齐分发目标，随后回查路由和分发状态" } };
  await expect(agent.report(await current(task.taskId), { ...handoff, phase: "assess" })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "assess" });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message",
    summary: "已发送指引，观察实际效果", evidence_ids: [sent.evidenceId] });
  await agent.report(await current(task.taskId), { ...handoff, phase: "observe" });
  expect(await current(task.taskId)).toMatchObject({ status: "waiting_owner", phase: "observe",
    ownerRequest: { reason: "configuration", status: "pending" } });
});

it("keeps terminal tasks with unfinished card synchronization in maintenance after a restart", async () => {
  const task = await create();
  await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1, status: "completed", ownerCardSyncPending: true,
    ownerRequest: { requestId: "r1", revision: 1, status: "confirmed", reason: "permission", summary: "权限", instructions: "已处理，等待实际核验", evidence_ids: [], resumePhase: "assess", createdAtMs: now } }));
  expect((await tasks.listForMaintenance(200)).map(value => value.taskId)).toContain(task.taskId);
  notifier.notifyOwnerRequest = vi.fn().mockResolvedValue(true);
  await service.maintenance();
  await vi.waitFor(async () => expect((await current(task.taskId)).ownerCardSyncPending).toBe(false));
  expect((await tasks.listForMaintenance(200)).map(value => value.taskId)).not.toContain(task.taskId);
  expect((await current(task.taskId)).status).toBe("completed");
});
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
  expect(notifier.notify).toHaveBeenCalledExactlyOnceWith({ ownerId: "owner", botId: "default", targetEnvironment: "prod",
    taskId: task.taskId, sessionKey: task.request.session_key, kind: "result", effect: "not_recovered", summary });
  expect(task.timeline.some(event => event.kind === "notification" && event.payload.kind === "result" && event.payload.sent === true)).toBe(true);
  const notice = actionNotifier.after.mock.calls.map(([value]) => value).find(value => value.kind === "result");
  expect(task.timeline.find(event => event.event_id === notice?.eventId)).toMatchObject({
    kind: "termination", summary, payload: { completion_reason: "owner_handoff" },
  });
  expect(task.timeline.some(event => event.kind === "plan" || event.kind === "owner_decision")).toBe(false);
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "completed", completion_reason: "owner_handoff", effect: "not_recovered", intervention_status: phase === "observe" ? "performed" : "none" });
  await service.executionFailure(task.taskId, "late AIS callback", task.execution!.executionId, true);
  await service.maintenance();
  expect(submissions).toHaveLength(1);
  await expect(agent.report(task, { kind: "owner_handoff", phase, summary, evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(notifier.notify).toHaveBeenCalledTimes(1);
});

it.each(["assess", "observe", "plan", "verify"].flatMap(phase => ["not_recovered", "unknown"].map(effect => ({ phase, effect }))))(
  "keeps the current $effect judgment when handing off from $phase without waiting or repeating work", async ({ phase, effect }) => {
    let task = await create();
    await appendFile(sessionFile, JSON.stringify({ type: "message", id: "task-progress", message: { role: "assistant", content: "已修正参数，子任务已完成并汇总。" } }) + "\n");
    const read = await inspect(task, "current-effect"); task = await current(task.taskId);
    task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase: phase as RecoveryStoredTask["phase"], version: value.version + 1,
      judgment: { delivery: "unconfirmed", response: "none", effect: "not_recovered", summary: "旧判断待复核", evidence_ids: [read.evidenceId] } })))!;
    const summary = "已核对本轮执行，原业务资源仍不可用，现有能力不能解除阻塞，请 Owner 处理资源。";
    await agent.report(task, { kind: "owner_handoff", phase, effect, summary, evidence_ids: [read.evidenceId] });
    task = await current(task.taskId);
    expect(task).toMatchObject({ status: "completed", completionReason: "owner_handoff", phase, plan: null,
      ownerDeadlineAt: null, observationDeadlineAt: null, execution: { state: "ended" },
      judgment: { effect, summary, delivery: "unconfirmed", response: "none", evidence_ids: [read.evidenceId] } });
    expect(notifier.notify).toHaveBeenCalledExactlyOnceWith({ ownerId: "owner", botId: "default", targetEnvironment: "prod",
      taskId: task.taskId, sessionKey: task.request.session_key, kind: "result", effect, summary });
    expect(runtime.sendMessage).not.toHaveBeenCalled();
    expect(task.timeline.some(event => event.kind === "plan" || event.kind === "owner_decision")).toBe(false);
    await service.reportResults();
    expect(callbacks.at(-1)).toMatchObject({ status: "completed", completion_reason: "owner_handoff", effect, intervention_status: "none" });
    await service.maintenance(); expect(submissions).toHaveLength(1);
  });

it.each(["acted", "replied"] as const)("saves confirmed delivery and %s on a direct Owner handoff from observation", async response => {
  let task = await toObserve(await create());
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "bot-response", message: { role: "assistant", content: "已按本次自愈指引重试，仍然受阻。" } }) + "\n");
  const read = await inspect(task, "handoff-current-response"); task = await current(task.taskId);
  expect(task.judgment).toBeNull();
  await agent.report(task, { kind: "owner_handoff", phase: "observe", delivery: "confirmed", response,
    effect: "not_recovered", summary: "指引已触达，Bot 重试后仍受阻，请 Owner 处理。", evidence_ids: [read.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", completionReason: "owner_handoff",
    judgment: { delivery: "confirmed", response, effect: "not_recovered" }, execution: { state: "ended" } });
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

it.each(["none", "unknown"] as const)("keeps unconfirmed delivery and response=%s when the handoff has no guidance", async response => {
  let task = await create(); const read = await inspect(task, "handoff-without-guidance"); task = await current(task.taskId);
  await agent.report(task, { kind: "owner_handoff", phase: "assess", delivery: "unconfirmed", response,
    effect: "unknown", summary: "没有发送指引，现有证据不足以确认恢复，请 Owner 排查。", evidence_ids: [read.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", judgment: { delivery: "unconfirmed", response, effect: "unknown" } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it.each([
  { delivery: "confirmed", response: "acted" },
  { delivery: "confirmed", response: "none" },
  { delivery: "unconfirmed", response: "acted" },
  { delivery: "unconfirmed", response: "replied" },
  { delivery: "confirmed" },
  { response: "none" },
  { delivery: null, response: "none" },
  { delivery: "unconfirmed", response: "invalid" },
])("rejects an unsupported or incomplete Owner handoff judgment %j", async judgment => {
  let task = await create();
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "old-guidance", message: { role: "user", content: "[会话自愈：old-delivery]\n历史指引" } }) + "\n");
  const read = await inspect(task, "handoff-unsupported-judgment"); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "owner_handoff", phase: "assess", ...judgment,
    effect: "unknown", summary: "不能把历史消息或未确认的行动归因于本次自愈。", evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", version: task.version });
  expect(notifier.notify).not.toHaveBeenCalled();
});

it.each([undefined, "not_recovered", "unknown"] as const)("uses saved effect %s for an older handoff, or unknown when no judgment exists", async effect => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  if (effect) task = (await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    judgment: { delivery: "unconfirmed", response: "none", effect, summary: "已有现场判断", evidence_ids: [read.evidenceId] } })))!;
  await agent.report(task, { kind: "owner_handoff", phase: "assess", summary: "请 Owner 查阅后续处理建议。", evidence_ids: [read.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", judgment: {
    effect: effect ?? "unknown", delivery: "unconfirmed", response: effect ? "none" : "unknown" } });
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
  await expect(agent.report(assessed, { ...blocked, kind: "owner_handoff", effect: "not_recovered" }))
    .rejects.toMatchObject({ status: 422, message: expect.stringContaining("assessment") });
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

function useNativeOwnerCards() {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const cards = new SessionRecoveryNotifier(sender, "https://pre.invalid", {
    findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ card: { pre: { template_id: "template", callback_route_key: "route" } } }) }),
  }, "pre");
  notifier.notify.mockImplementation(input => cards.notify(input));
  notifier.notifyOwnerRequest = vi.fn(input => cards.notifyOwnerRequest(input));
  return sender;
}

it("delivers all three material requirements while retaining the complete report and ending ordinary handoff", async () => {
  const sender = useNativeOwnerCards();
  let task = await create(); const read = await inspect(task, "materials-brief"); task = await current(task.taskId);
  const report = "## 调查与核实\n" + "原Bot已核实材料缺失，正确等待用户补充；现场和证据均已检查。\n".repeat(50);
  const handoff = { summary: "抽奖活动权益评审缺少必要材料，当前等待补充。", instructions: "请回原会话补充：\n1. 活动页面文案；\n2. 权益实际发放规则；\n3. 图1（领取路径截图）。" };
  await agent.report(task, { kind: "owner_handoff", phase: "assess", effect: "unknown", summary: report, handoff, evidence_ids: [read.evidenceId] });
  const saved = await current(task.taskId), card = sender.sendCard.mock.calls[0][0];
  expect(saved).toMatchObject({ status: "completed", completionReason: "owner_handoff", judgment: { effect: "unknown", summary: report }, execution: { state: "ended" } });
  expect(saved.ownerRequest).toBeUndefined();
  expect(saved.timeline.findLast(event => event.kind === "termination")).toMatchObject({ summary: report, payload: { handoff } });
  expect(card.cardParamMap).toMatchObject({ summary: handoff.summary, instructions: handoff.instructions, confirm_visible: "false" });
  expect(card.cardParamMap.instructions).toContain("3. 图1");
  expect(card.cardParamMap.summary).not.toContain("恢复情况尚未确认");
  expect(card).not.toHaveProperty("callbackRouteKey"); expect(card.cardParamMap).not.toHaveProperty("config");
  expect(runtime.sendMessage).not.toHaveBeenCalled(); expect(sender.sendCard).toHaveBeenCalledOnce();
});

it("uses the same Owner brief for permission handling and preserves its full investigation in the waiting timeline", async () => {
  const sender = useNativeOwnerCards();
  let task = await create(); const read = await inspect(task, "permission-brief"); task = await current(task.taskId);
  expect((await agent.bootstrap(task)).ownerHandoffProtocol).toBe("session-recovery-handoff/v1");
  const report = "## 权限调查\n" + "原会话和配置已核实，实际请求仍明确返回权限不足，详见完整证据。\n".repeat(45);
  const handoff = { summary: "原资料读取被权限拦截，信息查询尚未完成。", instructions: "请为原资料申请读取权限：https://docs.invalid/access/source 。" };
  await agent.report(task, { kind: "owner_handoff", phase: "assess", summary: report, handoff, owner_request: { reason: "permission" }, evidence_ids: [read.evidenceId] });
  await vi.waitFor(() => expect(sender.sendCard).toHaveBeenCalledOnce());
  const saved = await current(task.taskId), card = sender.sendCard.mock.calls[0][0];
  expect(saved).toMatchObject({ status: "waiting_owner", ownerRequest: { reason: "permission", summary: handoff.summary, instructions: handoff.instructions, status: "pending" } });
  expect(saved.timeline.find(event => event.kind === "waiting")).toMatchObject({ summary: report });
  expect(card).toMatchObject({ callbackRouteKey: "route", cardParamMap: { summary: handoff.summary, instructions: handoff.instructions, confirm_visible: "true" } });
  expect(card.cardParamMap.summary).not.toContain("权限调查"); expect(card.cardParamMap).not.toHaveProperty("config");
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it.each([
  { summary: "问题已明确", instructions: "材".repeat(342) },
  { summary: "问题已明确" },
  { summary: "问题已明确", instructions: "请补资料", template_id: "not-a-business-field" },
])("rejects invalid handoff content before changing workflow or notifying: %j", async handoff => {
  const sender = useNativeOwnerCards();
  let task = await create(); const read = await inspect(task, "invalid-brief"); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "owner_handoff", phase: "assess", summary: "完整调查报告", handoff, evidence_ids: [read.evidenceId] })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "assess" });
  expect(sender.sendCard).not.toHaveBeenCalled(); expect(runtime.sendMessage).not.toHaveBeenCalled();
});


it.each([
  { summary: undefined, owner_request: { reason: "permission" } },
  { summary: "完整调查报告", owner_request: { reason: "permission", instructions: "另一份不一致的处理事项" } },
])("rejects permission handoff without the full report or with conflicting actions: %j", async fields => {
  const sender = useNativeOwnerCards();
  let task = await create(); const read = await inspect(task, "invalid-permission-brief"); task = await current(task.taskId);
  await expect(agent.report(task, { kind: "owner_handoff", phase: "assess", handoff: { summary: "原资料读取缺少权限", instructions: "请申请原资料读取权限" },
    evidence_ids: [read.evidenceId], ...fields })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", phase: "assess" });
  expect(sender.sendCard).not.toHaveBeenCalled();
});


it.each(["assess", "observe", "plan", "verify"].flatMap(phase => [false, true].map(saved => ({ phase, saved }))))(
  "rejects recovered Owner handoff from $phase (saved judgment: $saved) without side effects", async ({ phase, saved }) => {
    let task = await create(); const read = await inspect(task, "recovered-evidence"); task = await current(task.taskId);
    task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase: phase as RecoveryStoredTask["phase"], version: value.version + 1,
      ...(saved ? { judgment: { delivery: "unconfirmed", response: "none", effect: "recovered", summary: "实际任务已恢复", evidence_ids: [read.evidenceId] } as const } : {}) })))!;
    const input = { summary: "任务已恢复，只剩 Skill 参数示例建议。", evidence_ids: [read.evidenceId], executionId: task.execution!.executionId,
      ...(saved ? {} : { effect: "recovered" as const }) };
    await expect(service.finishForOwnerHandoff(task.taskId, task.version, input)).rejects.toMatchObject({ status: 422 });
    expect(await current(task.taskId)).toMatchObject({ version: task.version, status: "running", phase, judgment: task.judgment, execution: task.execution });
    expect(notifier.notify).not.toHaveBeenCalled(); expect(actionNotifier.after).not.toHaveBeenCalled();
    await service.reportResults(); expect(callbacks).toEqual([]); expect(runtime.sendMessage).not.toHaveBeenCalled();
  });

it("does not turn an existing recovered result into another Owner permission wait", async () => {
  let task = await create(); const read = await inspect(task); task = await current(task.taskId);
  task = (await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    judgment: { delivery: "unconfirmed", response: "none", effect: "recovered", summary: "原任务已恢复", evidence_ids: [read.evidenceId] } })))!;
  await expect(service.requestOwner(task.taskId, task.version, { reason: "permission", instructions: "申请仅供优化的权限", summary: "优化建议",
    evidence_ids: [read.evidenceId], executionId: task.execution!.executionId })).rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", version: task.version });
  expect((await current(task.taskId)).ownerRequest).toBeUndefined();
});

it.each([false, true])("finishes recovery found during planning normally (pending delivery: %s)", async pending => {
  let task = await toObserve(await create()); const originalDeadline = task.observationDeadlineAt;
  const read = await inspect(task, "planning-recovery-evidence"); task = await current(task.taskId);
  task = (await tasks.update(task.taskId, task.version, value => ({ ...value, phase: "plan", observationDeadlineAt: null, version: value.version + 1 })))!;
  now += 120000;
  const judgment = { delivery: pending ? "unconfirmed" : "confirmed", response: pending ? "unknown" : "acted", effect: "recovered",
    summary: "原业务已恢复，参数模板可后续优化。", evidence_ids: [read.evidenceId] };
  await agent.report(task, { kind: "observation", phase: "plan", judgment, final_summary: "故障已解除，优化建议不阻塞恢复。" });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: pending ? "running" : "completed", phase: "observe", judgment, plan: null,
    observationDeadlineAt: originalDeadline });
  expect(task.timeline.some(event => event.kind === "termination")).toBe(false);
  expect(notifier.notify).not.toHaveBeenCalled(); expect(runtime.sendMessage).toHaveBeenCalledOnce();
  if (pending) {
    await service.reportResults(); expect(callbacks).toEqual([]);
    await agent.report(task, { kind: "observation", phase: "observe", judgment: { ...judgment, delivery: "confirmed", response: "acted" } });
  }
  expect(await current(task.taskId)).toMatchObject({ status: "completed" });
  expect((await current(task.taskId)).completionReason).toBeUndefined();
  await service.reportResults(); expect(callbacks.at(-1)).toMatchObject({ effect: "recovered", intervention_status: "performed" });
  expect(callbacks.at(-1)).not.toHaveProperty("completion_reason");
});

it("continues an ended failed TE Cron through the original session and completes after verified progress", async () => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" };
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: { runs: [{ status: "error", finishedAtMs: now - 60000 }] } } });
  const trajectory = { read: vi.fn(async (input: { messageMarker?: string }) => ({ source: "aivision_tclog", coverage: "uploaded_records_only",
    traces: [{ traceId: "original-trace", input: [{ role: "user", content: input.messageMarker ? input.messageMarker + "继续剩余步骤" : "生成原业务结果" }] }],
    ...(input.messageMarker ? { delivery_confirmed: true } : {}) })) };
  const teAgent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"), ais,
    runtime, targets: { resolve: async () => target }, trajectory, business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1,
    nasRoots: [], leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now, actionNotifier });
  let task = await create("approval", { target: { ...request.target, engine: "TE" }, session_key: "agent:main:cron:finished-run:user:owner" });
  const run = await teAgent.inspect(task, { clientRequestId: "finished-cron", purpose: "核实本轮失败结束", operation: "engine_read",
    request: { method: "GET", path: "/api/v1/bot/cron/cron-fixture/runs" } });
  expect(run.status).toBe("succeeded");
  await teAgent.trajectoryRead(await current(task.taskId), { clientRequestId: "original-failure", purpose: "核实失败位置和用户续聊" });
  const sent = await teAgent.retryMessage(await current(task.taskId), { clientRequestId: "continue-failed-cron", purpose: "纠正参数并续接剩余业务",
    message: "请补齐工具必填参数，复用已成功的数据，继续未完成步骤并核验结果。" });
  await teAgent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "本轮结束仍可按具体指引续接", evidence_ids: [sent.evidenceId] });
  const observed = await teAgent.trajectoryRead(await current(task.taskId), { clientRequestId: "continued-progress", purpose: "核实本次触达和业务恢复" });
  await teAgent.report(await current(task.taskId), { kind: "observation", phase: "observe", judgment: { delivery: "confirmed", response: "acted", effect: "recovered",
    summary: "原失败步骤成功，剩余业务已推进；优化建议附在结果中。", evidence_ids: [observed.evidenceId] } });
  task = await current(task.taskId); expect(task).toMatchObject({ status: "completed", judgment: { effect: "recovered" } });
  expect(task.completionReason).toBeUndefined();
  expect(runtime.sendMessage).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ sessionKey: task.request.session_key }));
  expect(runtime.executeShell).not.toHaveBeenCalled();
  expect(runtime.request).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ method: "GET", path: "/api/v1/bot/cron/cron-fixture/runs" }));
  expect(notifier.notify).not.toHaveBeenCalled();
});


it.each(["not_recovered", "unknown"] as const)("does not downgrade verified recovery to %s for Owner handoff", async effect => {
  let task = await toObserve(await create()); const read = await inspect(task); task = await current(task.taskId);
  task = (await tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
    judgment: { delivery: "unconfirmed", response: "unknown", effect: "recovered", summary: "业务效果已核实，等待触达", evidence_ids: [read.evidenceId] } })))!;
  await expect(agent.report(task, { kind: "owner_handoff", phase: "observe", effect, summary: "只有优化建议", evidence_ids: [read.evidenceId] }))
    .rejects.toMatchObject({ status: 422 });
  expect(await current(task.taskId)).toMatchObject({ status: "running", version: task.version, judgment: { effect: "recovered" }, observationDeadlineAt: task.observationDeadlineAt });
  expect(notifier.notify).not.toHaveBeenCalled(); expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

async function confirmPermissionRequest(task: RecoveryStoredTask) {
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { phase: task.phase, kind: "owner_handoff", summary: "缺服务权限", evidence_ids: [read.evidenceId],
    owner_request: { reason: "permission", instructions: "开通服务权限" } });
  const waiting = await current(task.taskId);
  await service.confirmOwner(task.taskId, waiting.ownerRequest!.requestId, actor);
  const fresh = await inspect(await current(task.taskId), "fresh-after-permission");
  expect(fresh.status).toBe("succeeded");
  return { task: await current(task.taskId), fresh };
}

const ownerContinuation = { clientRequestId: "owner-continue", purpose: "权限反馈后恢复原任务", message: "请重试已开通权限的原操作。" };

it("allows a new attempt after a pre-dispatch failure, and concurrent attempts share one dispatch", async () => {
  const { task } = await confirmPermissionRequest(await create());
  vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("temporary session locator failure"));
  const failed = await agent.retryMessage(task, ownerContinuation);
  expect(failed).toMatchObject({ status: "failed", result: { dispatched: false } });
  expect(await agent.retryMessage(await current(task.taskId), ownerContinuation)).toEqual(failed);
  expect(runtime.sendMessage).not.toHaveBeenCalled();

  const fresh = await current(task.taskId);
  const [retry, concurrent] = await Promise.all([
    agent.retryMessage(fresh, { ...ownerContinuation, clientRequestId: "owner-continue-2" }),
    agent.retryMessage(fresh, { ...ownerContinuation, clientRequestId: "owner-continue-3" }),
  ]);
  expect(retry.toolCallId).not.toBe(failed.toolCallId);
  expect(concurrent.toolCallId).toBe(retry.toolCallId);
  expect(await agent.toolGet(fresh, retry.toolCallId)).toMatchObject({ status: "succeeded", result: { status: "accepted" } });
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

it.each(["accepted", "unknown"] as const)("reuses the %s continuation receipt across executions", async status => {
  const { task } = await confirmPermissionRequest(await create());
  vi.mocked(runtime.sendMessage).mockResolvedValueOnce({ status });
  const sent = await agent.retryMessage(task, ownerContinuation);
  await agent.release(await current(task.taskId));
  await agent.start(await current(task.taskId));
  const replacement = await current(task.taskId);
  expect(replacement.execution!.executionId).not.toBe(task.execution!.executionId);

  expect(await agent.retryMessage(replacement, ownerContinuation)).toEqual(sent);
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
  await expect(agent.report(await current(task.taskId), { kind: "blocked", phase: "assess", summary: "续接效果未确认" })).rejects.toMatchObject({ status: 422 });
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message",
    summary: "继续观察本次投递结果", evidence_ids: [sent.evidenceId] });
  expect((await current(task.taskId)).phase).toBe("observe");
});

it.each(["blocked", "owner_handoff"])("allows %s when Owner reevaluation cannot proceed and only an earlier send exists", async kind => {
  const { task, fresh } = await confirmPermissionRequest(await toObserve(await create()));
  await agent.report(task, { kind, phase: "assess", summary: "续接仍受阻，需先处理剩余事项", evidence_ids: [fresh.evidenceId] });
  expect((await current(task.taskId)).status).toBe(kind === "blocked" ? "failed" : "completed");
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

it("rejects a message assessment based on an earlier send or a failed current continuation", async () => {
  const { task, fresh } = await confirmPermissionRequest(await toObserve(await create()));
  const assessment = { kind: "assessment", phase: "assess", decision: "message", summary: "报告本轮投递", evidence_ids: [fresh.evidenceId] };
  await expect(agent.report(task, assessment)).rejects.toMatchObject({ status: 422 });
  vi.mocked(runtime.sendMessage).mockResolvedValueOnce({ status: "failed" });
  const failed = await agent.retryMessage(task, ownerContinuation);
  expect(failed).toMatchObject({ status: "failed", result: { status: "failed" } });
  await expect(agent.report(await current(task.taskId), { ...assessment, evidence_ids: [failed.evidenceId] })).rejects.toMatchObject({ status: 422 });
  expect((await current(task.taskId)).phase).toBe("assess");
  await agent.report(await current(task.taskId), { ...assessment, decision: "observe" });
  expect((await current(task.taskId)).phase).toBe("observe");
});
