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

const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "dispatch-fixture", delivery_key: "initial-1", diagnosis: "原任务执行受阻",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
let db: SqliteDatabase, tasks: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService;
let ais: AisExecutor, runtime: BotRuntime;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  const repo = new EvolveRepository(db), objects = new Map<string, Buffer>();
  tasks = new SessionRecoveryTaskRepository(db, repo, {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
  });
  const target: ResolvedBotTarget = { provider: "arca", environment: "prod", ownerId: "owner", botId: "default",
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
afterEach(() => db.close());

it.each([null, "TC.MCP.PERMISSION"])("sends only the task output-token override on initial and resumed dispatches (%s)", async tc_fault_label => {
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
});
