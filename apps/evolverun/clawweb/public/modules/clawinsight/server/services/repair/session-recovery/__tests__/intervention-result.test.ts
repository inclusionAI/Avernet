import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, appendFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import type { RecoveryTaskResult, RecoveryPlan, RecoveryStoredTask } from "../task-contracts.js";
const exec = promisify(execFile);
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

it("reports none for investigation-only failures without adding a persisted task field", async () => {
  const task = await create();
  await inspect(task);
  await service.executionFailure(task.taskId, "无法继续调查");
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "failed", effect: "unknown", intervention_status: "none" });
  expect(JSON.parse((await repo.findTask(task.taskId))!.config_json).interventionStatus).toBeUndefined();
});
it.each([false, true])("keeps an unfinished write uncertain only after it is claimed: claimed=%s", async claimed => {
  const task = await create();
  const audit = new AgentToolCallRepository(db, "session_recovery");
  const input = { callId: "intervention-unfinished", taskId: task.taskId, stepId: task.current!.stepId, executionId: task.execution!.executionId,
    authorizationScopeDigest: task.authorizationScopeDigest!, clientRequestId: "intervention-unfinished", toolName: "retry_message", operation: "retry_message", isWrite: true, request: { purpose: "发送恢复建议" } };
  await audit.createToolCall(input);
  if (claimed) await audit.claimToolCall({ ...input, leaseOwner: "intervention-worker", leaseExpiresAt: Math.floor(now / 1000) + 60 });
  await service.terminate(task.taskId, actor);
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "canceled", intervention_status: claimed ? "unknown" : "none" });
});
it.each([false, true])("requires actual message presence, not channel acceptance: confirmed=%s", async confirmed => {
  const task = await create();
  await agent.retryMessage(task, { clientRequestId: "intervention-message", purpose: "提供原任务继续指引", message: "请依据工具返回继续" });
  if (confirmed) await inspect(await current(task.taskId));
  await service.executionFailure(task.taskId, "执行中断");
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "failed", effect: "unknown", intervention_status: confirmed ? "performed" : "unknown" });
});
it.each(["applied", "not_applied"] as const)("does not substitute generic write reconciliation for message proof: %s", async outcome => {
  const task = await create();
  vi.mocked(runtime.sendMessage).mockRejectedValueOnce(new Error("message receipt lost"));
  const sent = await agent.retryMessage(task, { clientRequestId: "intervention-unconfirmed-message", purpose: "提供恢复指引", message: "继续原任务" });
  const evidence = await inspect(await current(task.taskId), "intervention-message-check");
  expect(evidence.result.delivery_confirmed).toBe(false);
  await agent.reconcileWrite(await current(task.taskId), { clientRequestId: "intervention-message-reconcile", purpose: "记录已有核实结论", sourceToolCallId: sent.toolCallId,
    outcome, summary: "这项通用写入结论不能代替消息触达证明", evidenceToolCallIds: [evidence.toolCallId] });
  await service.executionFailure(task.taskId, "结束本次执行");
  await service.reportResults();
  expect(callbacks.at(-1)?.intervention_status).toBe("unknown");
});
it.each(["success", "nonzero", "lost", "applied", "not_applied"] as const)("derives environment intervention from execution and reconciliation: %s", async outcome => {
  let task = await create("automatic");
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "需要获批的环境动作", evidence_ids: [read.evidenceId] });
  await agent.report(await current(task.taskId), { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId);
  if (outcome === "nonzero") vi.mocked(runtime.executeShell).mockResolvedValueOnce({ status: "failed", exitCode: 1, stdout: "partial", stderr: "failed", durationMs: 1 });
  else if (outcome !== "success") vi.mocked(runtime.executeShell).mockRejectedValueOnce(new Error("receipt lost"));
  const call = await agent.apply(task, { actionId: "fix", clientRequestId: "intervention-action", purpose: "执行已批准动作" });
  if (outcome === "applied" || outcome === "not_applied") {
    const evidence = await inspect(await current(task.taskId), "intervention-check");
    await agent.reconcileWrite(await current(task.taskId), { clientRequestId: "intervention-reconcile", purpose: "依据后续现场核实", sourceToolCallId: call.toolCallId,
      outcome, summary: "依据后续读取回执核实动作", evidenceToolCallIds: [evidence.toolCallId] });
  }
  // Rehydrate with a new repository, as after a process restart.
  tasks = new SessionRecoveryTaskRepository(db, repo, artifactStore);
  expect((await agent.bootstrap(await current(task.taskId))).task.interventionStatus).toBeUndefined();
  await service.executionFailure(task.taskId, "执行结束");
  await service.reportResults();
  expect(callbacks.at(-1)?.intervention_status).toBe(outcome === "success" || outcome === "applied" ? "performed" : outcome === "not_applied" ? "none" : "unknown");
  expect((await service.detail(task.taskId, actor)).interventionStatus).toBeUndefined();
});
it("preserves unavailable historical audit after legacy migration and later termination", async () => {
  const { interventionStatus: _projection, ...task } = await create();
  const result = { event_id: task.request.event_id, task_id: task.taskId, result_version: task.version, status: "waiting_effect" as const,
    phase: "observe" as const, waiting_reason: "effect" as const, effect: "unknown" as const, result_text: "历史观察结果", evidence_ids: [] };
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify({ ...task, status: "waiting_effect", phase: "observe", pending_results: [result] }), task.taskId]);
  await db.exec("UPDATE ce_steps SET output_json = NULL WHERE task_id = ? AND step_no > 0", [task.taskId]);
  await service.reportResults();
  expect(callbacks).toEqual([result]);
  const migrated = await current(task.taskId);
  expect(migrated.interventionAuditComplete).toBeUndefined();
  expect(JSON.parse((await repo.findTask(task.taskId))!.config_json).interventionAuditComplete).toBeUndefined();
  expect((await agent.bootstrap(migrated)).task.interventionAuditComplete).toBeUndefined();
  expect((await service.detail(task.taskId, actor)).interventionAuditComplete).toBeUndefined();
  await service.terminate(task.taskId, actor);
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "canceled", intervention_status: "unknown" });
});

it("does not treat a pre-upgrade v2 artifact without audit coverage as no intervention", async () => {
  const task = await create();
  const stored = JSON.parse((await repo.findTask(task.taskId))!.config_json);
  const details = JSON.parse((await artifactStore.getObject(stored.detailsRef.objectKey)).content.toString());
  delete details.interventionAuditComplete;
  const content = Buffer.from(JSON.stringify(details)), objectKey = stored.detailsRef.objectKey + ".legacy";
  await artifactStore.putObject(objectKey, content, "application/json");
  stored.detailsRef = { objectKey, sha256: createHash("sha256").update(content).digest("hex") };
  await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(stored), task.taskId]);
  await service.executionFailure(task.taskId, "历史任务结束"); await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ status: "failed", intervention_status: "unknown" });
});


it("holds recovered results until delayed delivery evidence arrives, including after audit rehydration", async () => {
  let task = await create();
  vi.mocked(runtime.sendMessage).mockResolvedValueOnce({ status: "accepted", platformMessageId: "accepted-without-trace" });
  await agent.retryMessage(task, { clientRequestId: "delayed-send", purpose: "原任务继续指引", message: "继续原失败步骤" });
  const read = await inspect(await current(task.taskId), "before-ingestion");
  expect(read.result.delivery_confirmed).toBe(false);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "进入观察", evidence_ids: [read.evidenceId] });
  task = await current(task.taskId); const deadline = task.observationDeadlineAt;
  const judgment = { delivery: "unconfirmed", response: "unknown", effect: "recovered", summary: "原业务产物已恢复，触达证据尚未入库", evidence_ids: [read.evidenceId] };
  await agent.report(task, { kind: "observation", phase: "observe", judgment, final_summary: "应继续观察，不能提前收尾" });
  await service.reportResults();
  expect(callbacks).toEqual([]);
  expect(await current(task.taskId)).toMatchObject({ status: "running", judgment, observationDeadlineAt: deadline });
  await expect(agent.report(await current(task.taskId), { kind: "observation", phase: "observe",
    judgment: { ...judgment, delivery: "confirmed" } })).rejects.toMatchObject({ status: 422 });
  tasks = new SessionRecoveryTaskRepository(db, repo, artifactStore);
  let saved = await current(task.taskId);
  await tasks.update(saved.taskId, saved.version, value => ({ ...value, version: value.version + 1, timeline: [...value.timeline,
    ...Array.from({ length: 70 }, (_, i) => ({ event_id: `poll-${i}`, at_ms: now + i, kind: "operation" as const, actor: "agent" as const,
      summary: "观察", payload: { status: "succeeded", tool_call_id: `poll-${i}` } }))] }));
  const bootstrap = await agent.bootstrap(await current(task.taskId));
  expect(bootstrap.task.timeline.length).toBeLessThanOrEqual(60);
  expect(bootstrap.task.timeline.some(event => (event.payload.result as Record<string, unknown> | undefined)?.initial_message === true)).toBe(true);
  const sent = vi.mocked(runtime.sendMessage).mock.calls[0][0];
  await appendFile(sessionFile, JSON.stringify({ type: "message", id: "delayed-message", message: { role: "user", content: sent.message } }) + "\n");
  const after = await inspect(await current(task.taskId), "after-ingestion");
  expect(after.result.delivery_confirmed).toBe(true);
  saved = await current(task.taskId);
  await agent.report(saved, { kind: "observation", phase: "observe", judgment: { ...judgment, delivery: "confirmed", response: "acted", evidence_ids: [read.evidenceId, after.evidenceId] } });
  await service.reportResults();
  expect(await current(task.taskId)).toMatchObject({ status: "completed", observationDeadlineAt: deadline, judgment: { effect: "recovered" } });
  expect(callbacks.at(-1)).toMatchObject({ status: "completed", effect: "recovered", intervention_status: "performed" });
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
});

it.each([true, false])("waits for delayed TE AIVision evidence after recovery: initial=%s", async initial => {
  target = { ...target, provider: "teclaw", activeEngine: "teclaw" };
  let task = await create("automatic", { target: { ...request.target, engine: "TE" } });
  const read = vi.fn().mockResolvedValue({ delivery_confirmed: false, traces: [] });
  (agent as any).deps.trajectory = { read };
  const basis = await agent.trajectoryRead(task, { clientRequestId: "te-before-send", purpose: "核实原业务阻塞" });
  if (!initial) {
    await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "核实原任务", evidence_ids: [basis.evidenceId] });
    await agent.report(await current(task.taskId), { kind: "plan", phase: "plan", plan: { ...plan, actions: [], owner_cooperation: [] } });
  }
  await agent.retryMessage(await current(task.taskId), { clientRequestId: "te-delayed-send", purpose: "继续原任务", message: "请继续原失败步骤" });
  if (initial) await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message", summary: "等待指引效果", evidence_ids: [basis.evidenceId] });
  else await agent.report(await current(task.taskId), { kind: "apply", phase: "apply", applicable: true, success: true, summary: "发送方案内继续指引" });
  const phase = initial ? "observe" : "verify";
  const before = await agent.trajectoryRead(await current(task.taskId), { clientRequestId: "te-delayed-check", purpose: "核实消息和恢复效果" });
  const judgment = { delivery: "unconfirmed", response: "unknown", effect: "recovered", summary: "业务产物已恢复，AIVision暂未见本次消息", evidence_ids: [before.evidenceId] };
  await agent.report(await current(task.taskId), { kind: "observation", phase, judgment });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "running", phase, judgment });
  expect(task.pending_results).toEqual([]);
  const deadline = task.observationDeadlineAt;
  read.mockResolvedValue({ delivery_confirmed: true, traces: [] });
  const after = await agent.trajectoryRead(task, { clientRequestId: "te-late-ingestion", purpose: "补读迟到轨迹确认触达" });
  await agent.report(await current(task.taskId), { kind: "observation", phase, judgment: { ...judgment, delivery: "confirmed", evidence_ids: [after.evidenceId] } });
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ effect: "recovered", intervention_status: "performed" });
  expect((await current(task.taskId)).observationDeadlineAt).toBe(deadline);
  expect(runtime.sendMessage).toHaveBeenCalledOnce();
  expect(runtime.request).not.toHaveBeenCalled();
  expect(runtime.executeShell).not.toHaveBeenCalled();
});

it("counts durable Owner feedback once while keeping recovery unknown and preserving it across later requests", async () => {
  let task = await create();
  const read = await inspect(task);
  notifier.notifyOwnerRequest = vi.fn().mockResolvedValue(true);
  vi.spyOn(agent, "start").mockResolvedValue();
  const ask = async () => {
    task = await current(task.taskId);
    await service.requestOwner(task.taskId, task.version, { reason: "permission", summary: "原资料访问权限不足", instructions: "请申请原资料读取权限",
      evidence_ids: [read.evidenceId], executionId: task.execution!.executionId });
    task = await current(task.taskId);
    return task.ownerRequest!.requestId;
  };
  const first = await ask();
  await vi.waitFor(async () => expect((await current(task.taskId)).timeline.some(event => event.kind === "notification"
    && event.payload.channel === "owner_card" && event.payload.state === "sent")).toBe(true));
  expect(await current(task.taskId)).toMatchObject({ status: "waiting_owner", interventionStatus: "none", pending_results: [] });
  await expect(service.confirmOwner(task.taskId, first, { userId: "other", isAdmin: false })).rejects.toMatchObject({ status: 403 });
  await expect(service.confirmOwner(task.taskId, "stale-request", actor)).rejects.toMatchObject({ status: 409 });
  const save = vi.spyOn(tasks, "update").mockRejectedValueOnce(new Error("storage unavailable"));
  await expect(service.confirmOwner(task.taskId, first, actor)).rejects.toThrow("storage unavailable"); save.mockRestore();
  expect(await current(task.taskId)).toMatchObject({ interventionStatus: "none", ownerRequest: { status: "pending" } });

  await service.confirmOwner(task.taskId, first, actor, "已处理权限");
  await service.confirmOwner(task.taskId, first, actor, "重复回执");
  tasks = new SessionRecoveryTaskRepository(db, repo, artifactStore);
  expect(await current(task.taskId)).toMatchObject({ status: "running", interventionStatus: "performed", judgment: null, pending_results: [] });
  expect((await current(task.taskId)).timeline.filter(event => event.kind === "owner_decision")).toHaveLength(1);
  await inspect(await current(task.taskId), "after-owner-feedback");
  const second = await ask();
  expect(second).not.toBe(first);
  await expect(service.confirmOwner(task.taskId, first, actor)).rejects.toMatchObject({ status: 409 });
  expect(await current(task.taskId)).toMatchObject({ status: "waiting_owner", interventionStatus: "performed", ownerRequest: { requestId: second, status: "pending" } });
  await service.confirmOwner(task.taskId, second, actor, "已处理剩余权限");
  await service.executionFailure(task.taskId, "回执已保存，后续执行未完成核验");
  await service.reportResults();
  expect(callbacks.at(-1)).toMatchObject({ effect: "unknown", intervention_status: "performed" });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("does not count environment-plan approval as a confirmed permission-handling receipt", async () => {
  let task = await create();
  const read = await inspect(task);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "environment", summary: "需要获批环境动作", evidence_ids: [read.evidenceId] });
  await agent.report(await current(task.taskId), { kind: "plan", phase: "plan", plan });
  task = await current(task.taskId);
  vi.spyOn(agent, "start").mockResolvedValue();
  await service.decide(task.taskId, task.plan!.digest!, true, actor);
  expect((await current(task.taskId)).timeline.some(event => event.kind === "owner_decision")).toBe(true);
  expect((await current(task.taskId)).interventionStatus).toBe("none");
});
