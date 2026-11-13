import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, appendFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";
import type { RecoveryStoredTask } from "../task-contracts.js";
const exec = promisify(execFile);
const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "event-fixture", tc_fault_label: "TC.MCP.PERMISSION", delivery_key: "initial-1", diagnosis: "原 MCP 操作失败", error_location: "原会话第1行",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
let db: SqliteDatabase, repo: EvolveRepository, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService,
  runtime: BotRuntime, directory: string, sessionFile: string, now: number, target: ResolvedBotTarget, submissions: any[], ais: any, notifier: any, actionNotifier: { after: ReturnType<typeof vi.fn> };
let artifactStore: ConstructorParameters<typeof SessionRecoveryTaskRepository>[2];
async function diagnosisForRecheck() {
  await db.exec("CREATE TABLE insight_monitoring_diagnose (event_id TEXT PRIMARY KEY, self_healing_results TEXT)");
  await db.exec("INSERT INTO insight_monitoring_diagnose VALUES (?, NULL)", [request.event_id]);
}
async function finishOnlineAis(task: RecoveryStoredTask) {
  ais.getJobStatusDetail.mockResolvedValue({ status: "success", rawStatus: "SUCCEEDED" });
  await agent.handleAisStatus({ task: (await repo.findTask(task.taskId))!, step: (await repo.findStep(task.aisExecution!.stepId))!, report: { status: "succeeded" } });
}
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
beforeEach(async () => {
  now = Date.now(); submissions = [];
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
  const reporter = { report: vi.fn() };
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier, reporter, observationMs: 3600000, now: () => now, resolveTarget: async () => recoveryTargetSnapshot(target), actionNotifier });
});

it("rechecks a canceled task with original-session evidence and keeps its original business state", async () => {
  await diagnosisForRecheck();
  let task = await toObserve(await create());
  await inspect(task, "confirm-original-message");
  await service.terminate(task.taskId, actor);
  notifier.notify.mockClear();
  actionNotifier.after.mockClear();
  ais.getJobStatusDetail.mockResolvedValue({ status: "success", rawStatus: "SUCCEEDED" });
  task = await current(task.taskId);
  const requestId = "manual-recheck-fixture";
  await expect(service.startEffectRecheck(task.taskId, requestId, { userId: "other", isAdmin: false })).rejects.toMatchObject({ status: 403 });
  const ack = await service.startEffectRecheck(task.taskId, requestId, actor);
  expect(ack).toMatchObject({ status: "accepted", task_id: task.taskId });
  expect(await service.startEffectRecheck(task.taskId, requestId, actor)).toEqual(ack);
  expect(submissions).toHaveLength(2);
  expect(submissions[1].input.effectRecheck).toBe(true);
  const running = await current(task.taskId);
  expect(running).toMatchObject({ status: "canceled", phase: task.phase, request: task.request });
  expect(await agent.bootstrap(running)).toMatchObject({ effectRecheck: true, task: { status: "running", phase: "observe" },
    tools: { retry_message: false, apply_action: false, reconcile_write: false } });
  await expect(agent.retryMessage(running, {})).rejects.toMatchObject({ status: 403 });
  await expect(agent.report(running, { phase: "observe", kind: "plan" })).rejects.toMatchObject({ status: 422 });
  const read = await inspect(running, "historical-recheck-evidence");
  const judgment = { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原任务后续已恢复", evidence_ids: [read.evidenceId] };
  await expect(agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment: { ...judgment, evidence_ids: [] } }))
    .rejects.toMatchObject({ status: 422 });
  await agent.report(await current(task.taskId), { phase: "observe", kind: "observation", judgment });
  const saved = await current(task.taskId);
  expect(saved).toMatchObject({ status: "canceled", phase: task.phase, judgment, execution: { state: "ended" } });
  expect(saved.pending_results.at(-1)).toMatchObject({ task_id: task.taskId, event_id: request.event_id, status: "canceled",
    effect: "recovered", intervention_status: "performed", evidence_ids: [read.evidenceId] });
  expect(saved.timeline.find(event => event.payload.effect_recheck_outcome === "recovered")?.payload.result_version).toBe(saved.pending_results.at(-1)!.result_version);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(notifier.notify).not.toHaveBeenCalled();
  expect(actionNotifier.after).not.toHaveBeenCalled();
  await finishOnlineAis(saved);
  expect((await service.detail(task.taskId, actor)).executions.at(-1)).toMatchObject({ status: "succeeded", effectRecheckRequestId: requestId });
});

it("keeps a wait with another online round and discards a late supplement when policy reopens it", async () => {
  await diagnosisForRecheck();
  const config = new AppConfigRepository(db);
  await config.create({ config_key: "session_recovery_effect_resume_pre", config_json: '{"concurrency":1,"intervalSeconds":600,"windowSeconds":7200,"maxResumes":3}' });
  let task = await toObserve(await create());
  await inspect(task, "historical-touch");
  now += 3600001;
  task = await current(task.taskId);
  await service.observe(task.taskId, task.version, { delivery: "confirmed", response: "acted", effect: "unknown", summary: "尚未核实", evidence_ids: [] }, []);
  task = await current(task.taskId);
  await finishOnlineAis(task);
  await expect(service.startEffectRecheck(task.taskId, "while-online", actor)).rejects.toMatchObject({ code: "online_resume_available" });
  await db.exec("UPDATE ce_app_config SET enabled=0 WHERE config_key='session_recovery_effect_resume_pre'");
  await service.startEffectRecheck(task.taskId, "while-disabled", actor);
  const running = await current(task.taskId);
  await db.exec("UPDATE ce_app_config SET enabled=1 WHERE config_key='session_recovery_effect_resume_pre'");
  await agent.report(running, { kind: "observation", phase: "observe", judgment: { delivery: "confirmed", response: "unknown",
    effect: "recovered", summary: "配置已重新打开，本次补验不再写入", evidence_ids: [] } });
  const saved = await current(task.taskId);
  expect(saved).toMatchObject({ status: "waiting_effect", judgment: { effect: "unknown" } });
  expect(saved.timeline.find(event => event.payload.effect_recheck_outcome === "superseded")?.payload.reason).toBe("online_resume_available");
  expect(saved.pending_results.every(result => result.effect === "unknown")).toBe(true);
});

it("queues an existing source conclusion without starting AIS", async () => {
  await diagnosisForRecheck();
  let task = await toObserve(await create());
  const evidence = await inspect(task, "known-effect");
  await service.observe(task.taskId, (await current(task.taskId)).version,
    { delivery: "confirmed", response: "acted", effect: "recovered", summary: "原故障已解除", evidence_ids: [evidence.evidenceId!] }, []);
  task = await current(task.taskId);
  await finishOnlineAis(task);
  await service.reportPending(task);
  const result = await service.startEffectRecheck(task.taskId, "republish", actor);
  expect(result).toMatchObject({ status: "result_queued", task_id: task.taskId });
  expect(await service.startEffectRecheck(task.taskId, "republish", actor)).toEqual(result);
  expect(submissions).toHaveLength(1);
  const latest = (await current(task.taskId)).pending_results.at(-1)!;
  expect(latest).toMatchObject({ status: "completed", effect: "recovered" });
  await db.exec("UPDATE insight_monitoring_diagnose SET self_healing_results=? WHERE event_id=?",
    [JSON.stringify([latest]), request.event_id]);
  await expect(service.startEffectRecheck(task.taskId, "already-reported", actor)).rejects.toMatchObject({ code: "effect_already_known" });
});

it("lets the explicit online continuation replace an in-flight recheck without a stale write", async () => {
  await diagnosisForRecheck();
  let task = await toObserve(await create());
  await inspect(task, "known-original-touch");
  now += 3600001;
  task = await current(task.taskId);
  await service.observe(task.taskId, task.version, { delivery: "confirmed", response: "acted", effect: "unknown", summary: "尚未核实", evidence_ids: [] }, []);
  await finishOnlineAis(await current(task.taskId));
  await service.startEffectRecheck(task.taskId, "superseded", actor);
  const offline = await current(task.taskId);
  await service.resume(task.taskId);
  const online = await current(task.taskId);
  expect(online).toMatchObject({ status: "running", phase: "observe", judgment: { effect: "unknown" } });
  expect(online.execution?.executionId).not.toBe(offline.execution?.executionId);
  const judgment = { delivery: "unconfirmed", response: "unknown", effect: "unknown", summary: "证据不足", evidence_ids: [] };
  await expect(agent.report(offline, { phase: "observe", kind: "observation", judgment })).resolves.toEqual({ ok: true });
  expect((await current(task.taskId)).timeline.filter(event => event.payload.effect_recheck_outcome)).toHaveLength(1);
  await vi.waitFor(() => expect(ais.stopExecution).toHaveBeenCalledWith(offline.execution?.jobId, actor.userId));
});
