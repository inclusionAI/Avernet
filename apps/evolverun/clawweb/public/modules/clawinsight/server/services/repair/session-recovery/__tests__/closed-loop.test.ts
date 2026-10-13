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
import { createSessionRecoveryRouter } from "../../../../routes/session-recovery.js";
import { reconcileAgentExecutions } from "../../common/agent-execution-monitor.js";
import type { RecoveryTaskResult, RecoveryPlan, RecoveryStoredTask } from "../task-contracts.js";
const exec = promisify(execFile), flush = () => new Promise(resolve => setImmediate(resolve));
const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "event-fixture", tc_fault_label: "TC.MCP.PERMISSION", delivery_key: "initial-1", diagnosis: "原 MCP 操作失败", error_location: "原会话第1行",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
const plan: RecoveryPlan = { summary: "修正局部配置并重试", basis: "原工具调用失败，引用原会话证据", scope: "本任务目标的局部配置", verification: "原先失败的 MCP 操作成功并继续原任务",
  actions: [{ actionId: "fix", type: "container_command", command: "printf repaired", summary: "修正配置", risk: "局部修改", rollback: "恢复原值", verification: "读取配置确认" }], owner_cooperation: [] };
let db: SqliteDatabase, repo: EvolveRepository, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService,
  runtime: BotRuntime, directory: string, sessionFile: string, now: number, target: ResolvedBotTarget, submissions: any[], callbacks: RecoveryTaskResult[], ais: any, notifier: any, reporter: { report: (result: RecoveryTaskResult) => Promise<void> };
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
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite"); repo = new EvolveRepository(db); tasks = new SessionRecoveryTaskRepository(db, repo);
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
  agent = new SessionRecoveryAgentService({ tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"), ais, runtime, targets, business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1, nasRoots: [directory], leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now });
  reporter = { report: async result => { callbacks.push(result); } };
  service = new SessionRecoveryTaskService({ tasks, agent, notifier, reporter, observationMs: 3600000, ownerWaitMs: 3600000, now: () => now, resolveTarget: async () => recoveryTargetSnapshot(target) });
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
  expect(callbacks.at(-1)).toMatchObject({ effect: "recovered", delivery: { delivery_key: request.delivery_key } });
});
it("non-MCP goes to one environment plan without any initial retry message", async () => {
  let task = await create("approval", { tc_fault_label: "TC.SKILL.EXECUTION" });
  const read = await inspect(task, "read-skill-failure");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "环境文件有确定错误，需修复后继续", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); expect(task.phase).toBe("plan"); expect(runtime.sendMessage).not.toHaveBeenCalled();
  await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  expect(task.status).toBe("waiting_owner"); expect(notifier.notify).toHaveBeenCalledTimes(1);
  await service.decide(task.taskId, task.plan!.digest, true, actor);
  await agent.apply(await current(task.taskId), { actionId: "fix", clientRequestId: "fix-skill", purpose: "执行获批环境修复" });
  await agent.retryMessage(await current(task.taskId), { clientRequestId: "retry-skill", purpose: "修复后继续原任务", message: "局部错误已修正，请继续原任务" });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});
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
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "recovered-operation", message: { role: "assistant", content: "原MCP操作成功，继续原任务" } }) + "\n");
  const evidence = await inspect(await current(task.taskId), "read-verify"); expect((evidence.result as any).delivery.text).toContain("配置已核验");
  await agent.report(await current(task.taskId), { phase: "verify", kind: "observation", judgment: { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原失败操作已成功", evidence_ids: [evidence.evidenceId] } });
  const view = await service.detail(task.taskId, actor); expect(view.status).toBe("completed"); expect(view.evidence).toHaveLength(5); expect(view.timeline.some(event => event.kind === "result")).toBe(true); expect(view.execution).toBeUndefined(); expect(callbacks.at(-1)).toMatchObject({ status: "completed", effect: "recovered" }); expect(runtime.sendMessage).toHaveBeenCalledTimes(2);
  await service.annotate(task.taskId, actor, { layer: "effect", correct: true, reason: "原操作前后已核验", evidence_ids: [evidence.evidenceId] }); expect((await current(task.taskId)).timeline.some(event => event.kind === "annotation")).toBe(true);
});
it("Debug logs proposed actions without messages, writes or Owner notification", async () => {
  let task = await create("debug"); await expect(agent.apply(task, { actionId: "fix" })).rejects.toMatchObject({ status: 403 });
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "拟修复局部配置", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId); expect(task.status).toBe("completed"); expect(task.judgment!.effect).toBe("not_intervened"); expect(runtime.sendMessage).not.toHaveBeenCalled(); expect(notifier.notify).not.toHaveBeenCalled();
});
it("Owner expiry creates a new execution for the same task and isolates the old Base callback", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId); const oldStep = (await repo.findStep(task.aisExecution!.stepId))!, oldId = task.execution!.executionId;
  now += 3600001; await service.maintenance(); expect((await current(task.taskId)).execution!.state).toBe("ended"); await service.decide(task.taskId, task.plan!.digest, true, actor); task = await current(task.taskId); expect(submissions).toHaveLength(2); expect(task.execution!.executionId).not.toBe(oldId);
  expect(await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: oldStep, report: { status: "failed", summary: "旧容器收尾失败" } })).toMatchObject({ applied: true }); expect((await current(task.taskId)).execution!.executionId).toBe(task.execution!.executionId); expect((await current(task.taskId)).status).toBe("running");
});
it("late Owner approval retires the still-leased execution before maintenance or its successful Base callback", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const oldStep = (await repo.findStep(task.aisExecution!.stepId))!, oldId = task.execution!.executionId;
  const deadline = task.ownerDeadlineAt!;
  while (now < deadline - 1) { now = Math.min(now + 60000, deadline - 1); await agent.heartbeat(await current(task.taskId)); }
  now += 2;
  expect((await current(task.taskId)).execution!.leaseExpiresAt).toBeGreaterThan(Math.floor(now / 1000));
  await service.decide(task.taskId, task.plan!.digest, true, actor); task = await current(task.taskId);
  expect(submissions).toHaveLength(2); expect(task.execution!.executionId).not.toBe(oldId);
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: oldStep, report: { status: "succeeded", summary: "旧容器等待到期正常退出" } });
  expect((await current(task.taskId)).status).toBe("running");
  expect((await current(task.taskId)).execution!.executionId).toBe(task.execution!.executionId);
});
it("old Base completion during approval result reporting cannot fail the task before the new execution starts", async () => {
  let task = await toPlan(await create()); await agent.report(task, { kind: "plan", phase: "plan", plan }); task = await current(task.taskId);
  const oldStep = (await repo.findStep(task.aisExecution!.stepId))!, oldId = task.execution!.executionId;
  now = task.ownerDeadlineAt! + 1;
  let unblock!: () => void, reached = false;
  const pending = new Promise<void>(resolve => { unblock = resolve; });
  vi.spyOn(reporter, "report").mockImplementation(async result => {
    callbacks.push(result);
    if (result.status === "running" && result.phase === "apply") { reached = true; await pending; }
  });
  const approval = service.decide(task.taskId, task.plan!.digest, true, actor);
  await vi.waitFor(() => expect(reached).toBe(true));
  task = await current(task.taskId); expect(task.execution!.state).toBe("ended"); expect(task.status).toBe("running");
  try {
    await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: oldStep, report: { status: "succeeded", summary: "旧容器正常收尾" } });
    expect((await current(task.taskId)).status).toBe("running");
  } finally { unblock(); await approval; }
  task = await current(task.taskId);
  expect(task.status).toBe("running"); expect(task.execution!.executionId).not.toBe(oldId); expect(submissions).toHaveLength(2);
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
  const callback = callbacks.at(-1)!;
  expect(callback.status).toBe("failed"); expect(callback.effect).toBe("unknown");
  expect(callback.evidence_ids).toEqual(expect.arrayContaining([read.evidenceId, operation.evidenceId]));
  expect(callback.evidence?.some(item => item.tool_call_id === operation.toolCallId)).toBe(true);
});
it("effect waiting resumes the paused phase without resending advice", async () => {
  let task = await toObserve(await create()); const count = vi.mocked(runtime.sendMessage).mock.calls.length; expect(task.observationDeadlineAt).toBeTypeOf("number"); now = task.observationDeadlineAt! + 1; await service.maintenance(); task = await current(task.taskId); expect(task.status).toBe("waiting_effect"); await service.resume(task.taskId); task = await current(task.taskId); expect(task.phase).toBe("observe"); expect(task.requiresFreshObservation).toBe(true); await inspect(task, "read-resume"); expect((await current(task.taskId)).requiresFreshObservation).toBe(false); expect(runtime.sendMessage).toHaveBeenCalledTimes(count);
});
it("forced stop converges through the shared handler with unknown effect", async () => {
  let task = await create(); ais.getJobStatusDetail.mockResolvedValue({ status: "stopped", rawStatus: "STOPPED", errorMessage: null }); await reconcileAgentExecutions({ db, repo, handlers: { session_recovery: agent.handleAisStatus }, providers: { session_recovery: ais }, nowSeconds: () => Math.floor(now / 1000) }); task = await current(task.taskId); expect(task.status).toBe("failed"); expect(task.judgment!.effect).toBe("unknown"); expect(task.execution!.state).toBe("ended");
});
it("a failed plan cannot submit a second automatic plan", async () => {
  let task = await toPlan(await create("automatic")); await agent.report(task, { kind: "plan", phase: "plan", plan }); await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: false, success: false, summary: "最新现场已变化，原方案不适用" }); task = await current(task.taskId); expect(task.status).toBe("failed"); expect(callbacks.at(-1)!.effect).toBe("unknown"); await expect(service.plan(task.taskId, task.version, plan)).rejects.toMatchObject({ status: 409 });
});
it("uses the Engine reference when no actual request is supplied, only after approval", async () => {
  let task = await toPlan(await create());
  const enginePlan: RecoveryPlan = { ...plan, actions: [{ ...plan.actions[0], type: "engine_operation", command: undefined,
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
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" };
  let task = await create("approval", { target: { ...request.target, engine: "TE" } });
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: [{ role: "assistant", content: "原动作失败" }] } });
  const evidence = await inspect(task, "te-original");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "根据诊断需形成环境方案", evidence_ids: [evidence.evidenceId] });
  task = await current(task.taskId);
  await expect(agent.report(task, { kind: "plan", phase: "plan", plan })).rejects.toMatchObject({ status: 422 });
  expect(runtime.executeShell).not.toHaveBeenCalled();
  expect(runtime.request).toHaveBeenCalledTimes(1);
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
  expect(result.status).toBe("unknown"); expect(result.result).toMatchObject({ error: { code: "original_session_unavailable" } });
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
it("HTTP execution tickets and Owner identity guard reports, plan decisions and full evidence", async () => {
  const app = express(); app.use(createSessionRecoveryRouter({ service, tasks: service, agent, triggerToken: "fixture-service-key",
    resolveActor: async req => req.get("x-fixture-owner") ? { userId: req.get("x-fixture-owner")!, isAdmin: false } : null }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  try {
    const task = await toObserve(await create()), input = submissions[0].input, worker = `${origin}/internal/monitoring/session-recoveries/${task.taskId}/executions/${task.execution!.executionId}`;
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

it("allows an approved business action without a prewritten command, and audits each actual step", async () => {
  let task = await toPlan(await create());
  const actionPlan: RecoveryPlan = { ...plan, actions: [{ actionId: "fix", summary: "修正本任务配置", risk: "局部修改", verification: "读取配置确认", rollback: "恢复原值" }] };
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
  await agent.report(task, { kind: "plan", phase: "plan", plan: { ...plan, actions: [{ ...plan.actions[0], type: "engine_operation", command: undefined, engineOperation: reference }] } });
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
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" };
  let task = await create("approval", { target: { ...request.target, engine: "TE" } });
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: [] } });
  const inspected = await agent.inspect(task, { operation: "engine_read", request: { method: "GET", path: "/api/v1/bot/cron" }, clientRequestId: "read-cron", purpose: "核对诊断中的重复定时任务" });
  expect(inspected.status).toBe("succeeded");
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "诊断与现场需要清理重复定时任务", evidence_ids: [inspected.evidenceId] });
  task = await current(task.taskId);
  const actionPlan: RecoveryPlan = { ...plan, actions: [{ actionId: "remove-cron", summary: "删除已定位的重复日报任务", risk: "删除重复项，保留正常项", verification: "确认重复任务已不存在", rollback: "用原配置恢复" }] };
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
  expect(ais.stopExecution).toHaveBeenCalledWith("job-2");
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
  const body = { actionId: "fix", clientRequestId: "upload-content", purpose: "落实获批配置调整", engineOperation: { method: "POST", path: "/api/file/upload", upload: { targetPath: "/workspace/config.json", content: '{"enabled":true}' } } };
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
