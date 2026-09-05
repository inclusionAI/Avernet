import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { type IDatabase, SqliteDatabase, runMigrations } from "../../db.js";
import { EvolveRepository } from "../evolve-repository.js";

describe("Evolve Pack registry", () => {
  let db: SqliteDatabase;
  let repo: EvolveRepository;

  beforeEach(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    repo = new EvolveRepository(db);
  });

  afterEach(async () => {
    await db.close();
  });

  it("registers a Pack idempotently and links Restore tasks", async () => {
    const input = {
      pack_id: "PACK-1", user_id: "user-1", bot_id: "bot-1",
      source_task_id: "EV-SOURCE", source_step_id: "STEP-SOURCE",
      source_kind: "snapshot" as const, source_round: 0,
      artifact_ref: "oss://clawevolve-artifacts/evolution/EV-SOURCE/snapshots/artifact.zip",
      artifact_size: 42, artifact_sha256: "a".repeat(64), artifact_content_type: "application/zip",
    };
    const first = await repo.registerPack(input);
    const duplicate = await repo.registerPack({ ...input, pack_id: "PACK-IGNORED" });
    expect(duplicate.pack_id).toBe(first.pack_id);
    expect(await repo.listPacks("user-1")).toEqual([expect.objectContaining({ pack_id: first.pack_id })]);

    await repo.createTask({
      taskId: "EV-RESTORE", taskType: "pack_restore", userId: "user-1", botId: "bot-1",
      taskName: "restore", configJson: JSON.stringify({ packId: first.pack_id }), createdBy: "user-1",
    });
    expect((await repo.listPackApplications(first)).map((task) => task.task_id)).toEqual(["EV-RESTORE"]);
    expect(await repo.countPackApplications([first])).toEqual({ [first.pack_id]: 1 });
  });

  it.each(["mysql", "zdas"] as const)(
    "uses MySQL-compatible upsert syntax for %s",
    async (dbType) => {
      const input = {
        pack_id: "PACK-1", user_id: "user-1", bot_id: "bot-1",
        source_task_id: "EV-SOURCE", source_step_id: "STEP-SOURCE",
        source_kind: "snapshot" as const, source_round: 0,
        artifact_ref: "oss://clawevolve-artifacts/evolution/EV-SOURCE/snapshots/artifact.zip",
        artifact_size: 42, artifact_sha256: "a".repeat(64), artifact_content_type: "application/zip",
      };
      const exec = vi.fn().mockResolvedValue({ affectedRows: 1 });
      const query = vi.fn().mockResolvedValue([{ ...input, id: 1, status: "available", gmt_create: 0, gmt_modified: 0 }]);
      const mysqlCompatibleRepo = new EvolveRepository({ dbType, exec, query } as unknown as IDatabase);

      await mysqlCompatibleRepo.registerPack(input);

      expect(exec).toHaveBeenCalledWith(expect.stringContaining("ON DUPLICATE KEY UPDATE"), expect.any(Array));
      expect(exec.mock.calls[0][0]).not.toContain("ON CONFLICT");
    },
  );

  it("backfills Packs from historical successful Steps", async () => {
    await repo.createTask({
      taskId: "EV-HISTORICAL", taskType: "pack", userId: "user-1", botId: "bot-1",
      taskName: "historical", configJson: "{}", createdBy: "user-1",
    });
    await repo.createStep({
      stepId: "STEP-HISTORICAL", taskId: "EV-HISTORICAL", stepType: "pack", stepNo: 1,
      command: "/clawevolve-pack --mode pack",
    });
    await repo.updateStepStatus("STEP-HISTORICAL", {
      status: "succeeded",
      output: { pack: { status: "available", artifact: {
        kind: "pack",
        ref: "oss://clawevolve-artifacts/evolution/EV-HISTORICAL/snapshots/artifact.zip",
        size: 42, sha256: "c".repeat(64), contentType: "application/zip",
      } } },
    });

    await db.exec("DROP TABLE ce_packs");
    await db.exec("DELETE FROM schema_version WHERE version >= 90");
    await runMigrations(db, "sqlite");

    expect(await repo.listPacks("user-1")).toEqual([
      expect.objectContaining({
        source_task_id: "EV-HISTORICAL", source_step_id: "STEP-HISTORICAL",
        source_kind: "snapshot", source_round: 0,
      }),
    ]);
  });
});
