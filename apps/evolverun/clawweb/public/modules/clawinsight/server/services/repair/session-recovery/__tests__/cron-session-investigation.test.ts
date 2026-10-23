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
beforeEach(async () => {
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
  runtime = { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn(async input => {
    const output = await promisify(execFile)("/bin/bash", ["-c", input.command], { env: { ...process.env, OPENCLAW_STATE_DIR: home }, timeout: 10000 });
    return { status: "success", exitCode: 0, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
  }) };
  const ais = { execute: vi.fn().mockResolvedValue("job"), getJobStatusDetail: vi.fn(), stopExecution: vi.fn(), getJobStatus: vi.fn() };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks, repo, audit: new AgentToolCallRepository(db, "session_recovery"),
    ais, runtime, targets: { resolve: async () => target }, business: () => service, publicBaseUrl: "https://service.invalid", snapshotId: 1,
    leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000 });
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent, notifier: { notify: async () => false },
    reporter: { report: async () => {} }, observationMs: 3600000, resolveTarget: async () => recoveryTargetSnapshot(target) });
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
