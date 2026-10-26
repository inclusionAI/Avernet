import Database from "better-sqlite3";
import { expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskRepository, type RecoveryArtifactStore } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { createSessionRecoveryRuntime } from "../index.js";
import { SESSION_RECOVERY_HANDBOOKS_KEY } from "../handbook.js";

it("freezes DB team routes through production assembly, SQLite persistence, bootstrap and idempotent creation", async () => {
  const db = new SqliteDatabase(new Database(":memory:"));
  try {
    await runMigrations(db, "sqlite");
    const repo = new EvolveRepository(db), config = new AppConfigRepository(db), objects = new Map<string, Buffer>();
    const artifacts: RecoveryArtifactStore = {
      putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
      getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
    };
    const target: ResolvedBotTarget = { provider: "arca", environment: "prod", ownerId: "owner", botId: "default",
      deviceId: "fixture", bindingId: "binding", sandboxId: "sandbox", activeEngine: "openclaw" };
    const botRuntime: BotRuntime = { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() };
    const recovery = createSessionRecoveryRuntime({ db, repo, runtime: botRuntime, targets: { resolve: async () => target },
      ais: { execute: async () => "fixture-job" } as AisExecutor, publicBaseUrl: "https://fixture.invalid",
      snapshotId: 1, artifactStore: artifacts, nasRoots: [] })!;
    const request = { event_id: "event", delivery_key: "initial", tc_fault_label: "TC.MCP.PERMISSION", diagnosis: "工具调用失败",
      target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
    const actor = { userId: "owner", isAdmin: false }, stored = new SessionRecoveryTaskRepository(db, repo, artifacts);
    const create = async (event_id: string) => {
      const ack = await recovery.service.create({ ...request, event_id, delivery_key: event_id }, actor);
      await vi.waitFor(async () => expect((await stored.find(ack.task_id))?.execution?.jobId).toBe("fixture-job"));
      return ack;
    };
    const common = await create("common");
    expect((await recovery.agent!.bootstrap((await stored.find(common.task_id))!)).handbook).toMatchObject({
      handbookVersion: "clawevolve-recovery-handbook/v3", teamKey: null,
      chapters: ["common/mcp/handbook.md", "common/file-download/handbook.md"],
    });
    await config.create({ config_key: SESSION_RECOVERY_HANDBOOKS_KEY, config_json: JSON.stringify({
      bot_teams: [{ env: "prod", bot_id: "default", owner_id: "owner", team_key: "team-a" }],
      team_routes: { "team-a": { "TC.MCP": ["skill/handbook.md"] } },
    }) });
    const task = await create("team"), expected = { teamKey: "team-a",
      chapters: ["custom/team-a/skill/handbook.md", "common/mcp/handbook.md", "common/file-download/handbook.md"] };
    expect((await recovery.agent!.bootstrap((await stored.find(task.task_id))!)).handbook).toMatchObject(expected);
    await config.update(SESSION_RECOVERY_HANDBOOKS_KEY, { config_json: JSON.stringify({ invalid: true }) });
    expect(await recovery.service.create({ ...request, event_id: "team", delivery_key: "team" }, actor)).toMatchObject({ task_id: task.task_id });
    expect((await recovery.agent!.bootstrap((await stored.find(task.task_id))!)).handbook).toMatchObject(expected);
    await expect(create("another")).rejects.toMatchObject({ status: 503, code: "handbook_configuration_invalid" });
    expect(botRuntime.sendMessage).not.toHaveBeenCalled();
  } finally { await db.close(); }
});
