import Database from "better-sqlite3";
import { expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { BotSessionTrajectoryReader, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository, type RecoveryArtifactStore } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { createSessionRecoveryRuntime } from "../index.js";

it.each([
  { engine: "TE", activeEngine: "teclaw", hint: { trace_id: "fault-trace" }, expected: { traceId: "fault-trace" } },
  { engine: "OC", activeEngine: "openclaw", hint: { fault_at_ms: 1792107675000 }, expected: { faultAtMs: 1792107675000 } },
] as const)("dispatches $engine immediately and passes the diagnostic hint to assessment reads", async ({ engine, activeEngine, hint, expected }) => {
  const db = new SqliteDatabase(new Database(":memory:"));
  try {
    await runMigrations(db, "sqlite");
    const repo = new EvolveRepository(db), objects = new Map<string, Buffer>();
    const target: ResolvedBotTarget = { provider: "baas", environment: "prod", ownerId: "owner", botId: "default",
      deviceId: "fixture", bindingId: "binding", activeEngine };
    const trajectory = { read: vi.fn<BotSessionTrajectoryReader["read"]>()
      .mockResolvedValueOnce({ traces: [], observations: [] })
      .mockResolvedValueOnce({ trace: { traceId: "fault-trace" }, observations: [] }) };
    const execute = vi.fn<AisExecutor["execute"]>().mockResolvedValue("fixture-job");
    const ais = { execute, getJobStatus: vi.fn(), getJobStatusDetail: vi.fn(), stopExecution: vi.fn() };
    const artifactStore: RecoveryArtifactStore = {
      putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
      getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
    };
    const stored = new SessionRecoveryTaskRepository(db, repo, artifactStore);
    const runtime = createSessionRecoveryRuntime({
      db, repo, runtime: { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() },
      targets: { resolve: async () => target }, trajectory, ais, nasRoots: [],
      publicBaseUrl: "https://fixture.invalid", snapshotId: 1, artifactStore,
    })!;
    const { task_id } = await runtime.service.create({
      event_id: "late-trajectory", delivery_key: "late-trajectory", diagnosis: "原任务受阻", tc_fault_label: null,
      target: { env: "prod", entity_id: "owner", bot_id: "default", engine },
      session_key: "agent:main:session:original", ...hint,
    }, { userId: "owner", isAdmin: false });
    await vi.waitFor(async () => expect((await stored.find(task_id))?.execution?.jobId).toBe("fixture-job"));
    expect(execute).toHaveBeenCalledOnce();
    expect(trajectory.read).not.toHaveBeenCalled();

    const task = (await stored.find(task_id))!;
    expect(await runtime.agent!.bootstrap(task)).toMatchObject({
      task: { phase: "assess", request: hint }, tools: { trajectory_read: true },
    });
    const read = (clientRequestId: string) => runtime.agent!.trajectoryRead(task, {
      clientRequestId, purpose: "核实原会话故障轨迹", ...(engine === "TE" ? hint : {}), limit: 1,
    });
    expect(await read("first-read")).toMatchObject({ status: "succeeded", result: { traces: [] } });
    expect(trajectory.read).toHaveBeenCalledWith(expect.objectContaining(expected));
    expect(await read("followup-read")).toMatchObject({ status: "succeeded", result: { trace: { traceId: "fault-trace" } } });
    expect(await stored.find(task_id)).toMatchObject({ status: "running", phase: "assess" });
    expect(execute).toHaveBeenCalledOnce();
  } finally {
    await db.close();
  }
});
