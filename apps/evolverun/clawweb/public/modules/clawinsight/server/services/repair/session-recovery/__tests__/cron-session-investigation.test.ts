import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "../agent-service.js";

const sessionId = "historic-run", sessionKey = `agent:main:cron:daily:run:${sessionId}`;
const request = { event_id: "cron-failure", tc_fault_label: null, delivery_key: "advice", diagnosis: "Original operation failed",
  session_key: sessionKey, target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" } };
let db: SqliteDatabase, home: string, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService;
let agent: SessionRecoveryAgentService, runtime: BotRuntime;
let now: number;
beforeEach(async () => {
  now = Date.now();
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  const repo = new EvolveRepository(db), objects = new Map<string, Buffer>();
  tasks = new SessionRecoveryTaskRepository(db, repo, {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
  });
  home = await mkdtemp(join(tmpdir(), "recovery-cron-investigation-"));
  const directory = join(home, "agents/main/sessions"); await mkdir(directory, { recursive: true });
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId: "next-run" } }));
  await writeFile(join(directory, `${sessionId}.jsonl`), [
    { type: "session", id: sessionId }, { type: "message", id: "original-fault", message: { role: "assistant", content: "Original operation failed" } },
  ].map(value => JSON.stringify(value)).join("\n") + "\n");
  const target: ResolvedBotTarget = { provider: "arca", environment: "prod", ownerId: "owner", botId: "default", deviceId: "device",
    bindingId: "binding", sandboxId: "sandbox", activeEngine: "openclaw" };
  runtime = { sendMessage: vi.fn(), request: vi.fn().mockResolvedValue({ status: 200, body: { success: true, data: { running: [] } } }), executeShell: vi.fn(async input => {
    const output = await promisify(execFile)("/bin/bash", ["-c", input.command], { env: { ...process.env, OPENCLAW_STATE_DIR: home }, timeout: 10000 });
    return { status: "success", exitCode: 0, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
  }) };
  const ais = { execute: vi.fn().mockResolvedValue("job"), getJobStatusDetail: vi.fn(), stopExecution: vi.fn(), getJobStatus: vi.fn() };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"),
    ais, runtime, targets: { resolve: async () => target }, business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1,
    leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, now: () => now });
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier: { notify: async () => false },
    reporter: { report: async () => {} }, observationMs: 3600000, resolveTarget: async () => recoveryTargetSnapshot(target), now: () => now });
});
afterEach(async () => { await rm(home, { recursive: true, force: true }); db.close(); });

it.each([true, false])("investigates the original Cron run without creating a message session (explicit ID: %s)", async explicit => {
  const { task_id } = await service.create({ ...request, ...(explicit ? { session_id: sessionId } : {}) }, { userId: "owner", isAdmin: false });
  await vi.waitFor(async () => expect((await tasks.find(task_id))!.execution?.jobId).toBe("job"));
  const task = (await tasks.find(task_id))!;
  expect((await agent.bootstrap(task)).task.request.session_id).toBe(explicit ? sessionId : undefined);
  const read = await agent.sessionRead(task, { clientRequestId: "read", purpose: "Investigate the original failure", from_line: 1 });
  expect(read).toMatchObject({ status: "succeeded", result: { session_id: sessionId, messages: [{ line_no: 1 }, { record: { id: "original-fault" } }] } });
  const send = await agent.retryMessage((await tasks.find(task_id))!, { clientRequestId: "send", purpose: "Continue the original task", message: "Continue" });
  expect(send).toMatchObject({ status: "failed", result: { dispatched: false, error: { code: "original_session_unavailable" } } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("delivers to the verified persisted Cron conversation and retains the upstream identity", async () => {
  const base = "agent:main:cron:daily";
  await writeFile(join(home, "agents/main/sessions/sessions.json"), JSON.stringify({ [base]: { sessionId } }));
  vi.mocked(runtime.sendMessage).mockResolvedValue({ status: "accepted", platformMessageId: "delivered" });
  const { task_id } = await service.create({ ...request, session_id: sessionId }, { userId: "owner", isAdmin: false });
  await vi.waitFor(async () => expect((await tasks.find(task_id))!.execution?.jobId).toBe("job"));
  const sent = await agent.retryMessage((await tasks.find(task_id))!, { clientRequestId: "send", purpose: "Continue original", message: "Continue" });
  expect(sent).toMatchObject({ status: "succeeded", result: { session_key: base } });
  expect(runtime.sendMessage).toHaveBeenCalledWith(expect.objectContaining({ sessionKey: base, deliveryKey: request.delivery_key }));
  expect((await tasks.find(task_id))!.request).toMatchObject({ session_key: sessionKey, session_id: sessionId });
});

async function createCron() {
  const { task_id } = await service.create({ ...request, session_id: sessionId }, { userId: "owner", isAdmin: false });
  await vi.waitFor(async () => expect((await tasks.find(task_id))!.execution?.jobId).toBe("job"));
  return task_id;
}
async function readRunning(taskId: string, id: string) {
  return agent.inspect((await tasks.find(taskId))!, { clientRequestId: id, purpose: "Read the original Cron execution status",
    operation: "engine_read", request: { method: "GET", path: "/api/cron/running" } }) as Promise<{ evidenceId: string }>;
}
function running() {
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: { running: [{ id: "daily", running_at_ms: now }] } } });
}
async function waitForCron(taskId: string, evidence: string) {
  return agent.report((await tasks.find(taskId))!, { kind: "assessment", phase: "assess", decision: "wait_cron",
    summary: "The original Cron is still executing; observe before intervening", evidence_ids: [evidence] });
}

it("does not deliver into an executing Cron even when its conversation is already indexed", async () => {
  running();
  await writeFile(join(home, "agents/main/sessions/sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId } }));
  const id = await createCron();
  const result = await agent.retryMessage((await tasks.find(id))!, { clientRequestId: "send-running", purpose: "Continue original", message: "Continue" });
  expect(result).toMatchObject({ status: "failed", result: { dispatched: false, error: { code: "cron_run_running" } } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("retains one five-minute pre-intervention window through repeated reports and hands off without an effect claim", async () => {
  running();
  const id = await createCron(), first = await readRunning(id, "running-1"), startedAt = now;
  await waitForCron(id, first.evidenceId);
  expect(await tasks.find(id)).toMatchObject({ status: "running", phase: "assess", cronWait: { startedAt, deadlineAt: startedAt + 300000 }, judgment: null });
  now += 60000;
  const next = await readRunning(id, "running-2");
  await waitForCron(id, next.evidenceId);
  expect((await tasks.find(id))!.cronWait).toEqual({ startedAt, deadlineAt: startedAt + 300000 });
  now = startedAt + 300000;
  await expect(waitForCron(id, next.evidenceId)).rejects.toMatchObject({ status: 422 });
  await agent.report((await tasks.find(id))!, { kind: "owner_handoff", phase: "assess", delivery: "unconfirmed", response: "none", effect: "unknown",
    summary: "Observed the daily Cron for five minutes. It is still executing; Owner should inspect the pending SQL result. No message was sent.", evidence_ids: [next.evidenceId] });
  expect(await tasks.find(id)).toMatchObject({ status: "completed", completionReason: "owner_handoff", plan: null,
    judgment: { delivery: "unconfirmed", response: "none", effect: "unknown" }, execution: { state: "ended" } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("returns to normal recovery after the Cron ends without carrying the five-minute deadline into effect observation", async () => {
  running();
  const id = await createCron(), read = await readRunning(id, "running");
  await waitForCron(id, read.evidenceId);
  now += 60000;
  vi.mocked(runtime.request).mockResolvedValue({ status: 200, body: { success: true, data: { running: [] } } });
  await writeFile(join(home, "agents/main/sessions/sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId } }));
  vi.mocked(runtime.sendMessage).mockResolvedValue({ status: "accepted", platformMessageId: "delivery" });
  const sent = await agent.retryMessage((await tasks.find(id))!, { clientRequestId: "send-ended", purpose: "Continue the failed original operation", message: "Check the existing SQL result, then continue only the unfinished step" }) as { evidenceId: string };
  await agent.report((await tasks.find(id))!, { kind: "assessment", phase: "assess", decision: "message", summary: "Cron ended with a confirmed failed operation; guidance sent", evidence_ids: [read.evidenceId, sent.evidenceId] });
  const task = (await tasks.find(id))!;
  expect(task).toMatchObject({ status: "running", phase: "observe", observationDeadlineAt: now + 3600000 });
  expect(task.cronWait).toBeUndefined();
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});

it.each([
  { status: 503, body: { success: false } },
  { status: 200, body: { success: true, data: {} } },
])("keeps unavailable or malformed Cron status distinct from a confirmed running job: %j", async response => {
  vi.mocked(runtime.request).mockResolvedValue(response);
  await writeFile(join(home, "agents/main/sessions/sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId } }));
  const id = await createCron();
  const result = await agent.retryMessage((await tasks.find(id))!, { clientRequestId: "unknown-status", purpose: "Continue original", message: "Continue" });
  expect(result).toMatchObject({ status: "failed", result: { dispatched: false, error: { code: "cron_state_unavailable" } } });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("finishes a Cron that completed during the window without sending or attributing its progress to guidance", async () => {
  running();
  const id = await createCron(), status = await readRunning(id, "initial-status");
  await waitForCron(id, status.evidenceId);
  now += 60000;
  await writeFile(join(home, `agents/main/sessions/${sessionId}.jsonl`), [
    { type: "session", id: sessionId }, { type: "message", message: { role: "assistant", content: "Original operation succeeded; Cron completed" } },
  ].map(row => JSON.stringify(row)).join("\n") + "\n");
  const read = await agent.sessionRead((await tasks.find(id))!, { clientRequestId: "completed-read", purpose: "Verify completion of the original operation", from_line: 1 }) as { evidenceId: string };
  await agent.report((await tasks.find(id))!, { kind: "assessment", phase: "assess", decision: "observe", summary: "The original operation completed on its own", evidence_ids: [read.evidenceId] });
  await agent.report((await tasks.find(id))!, { kind: "observation", phase: "observe", judgment: {
    delivery: "unconfirmed", response: "none", effect: "recovered", summary: "The original operation succeeded independently", evidence_ids: [read.evidenceId] } });
  expect(await tasks.find(id)).toMatchObject({ status: "completed", judgment: { response: "none", effect: "recovered" } });
  expect((await tasks.find(id))!.cronWait).toBeUndefined();
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("preserves the original window when the AIS execution is released and replaced", async () => {
  running();
  const id = await createCron(), read = await readRunning(id, "initial-read");
  await waitForCron(id, read.evidenceId);
  const previous = (await tasks.find(id))!;
  await agent.release(previous);
  now += 60000;
  await agent.start((await tasks.find(id))!);
  const replacement = (await tasks.find(id))!;
  expect(replacement.execution!.executionId).not.toBe(previous.execution!.executionId);
  const next = await readRunning(id, "replacement-read");
  await waitForCron(id, next.evidenceId);
  expect((await tasks.find(id))!.cronWait).toEqual(previous.cronWait);
});

it("rejects Cron waiting after an initial message has been accepted", async () => {
  const id = await createCron();
  await writeFile(join(home, "agents/main/sessions/sessions.json"), JSON.stringify({ "agent:main:cron:daily": { sessionId } }));
  vi.mocked(runtime.sendMessage).mockResolvedValue({ status: "accepted", platformMessageId: "delivery" });
  const sent = await agent.retryMessage((await tasks.find(id))!, { clientRequestId: "already-sent", purpose: "Continue original", message: "Continue" }) as { evidenceId: string };
  await expect(waitForCron(id, sent.evidenceId)).rejects.toMatchObject({ status: 422 });
  expect((await tasks.find(id))!.delivery).toBeDefined();
});

it.each([
  { session_key: "agent:main:chat", execution_level: "approval" },
  { session_key: sessionKey, execution_level: "debug" },
])("does not put ordinary conversations or Debug tasks into Cron waiting: %j", async input => {
  const { task_id } = await service.create({ ...request, ...input }, { userId: "owner", isAdmin: false });
  await vi.waitFor(async () => expect((await tasks.find(task_id))!.execution?.jobId).toBe("job"));
  const read = await readRunning(task_id, "status-evidence");
  await expect(waitForCron(task_id, read.evidenceId)).rejects.toMatchObject({ status: 422 });
  expect((await tasks.find(task_id))!.cronWait).toBeUndefined();
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});
