import { describe, expect, it } from "vitest";
import type { ExecResult, IDatabase } from "@avernet/clawweb-shared/server/db";
import { mysqlDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { EvolveRepository } from "../evolve-repository.js";

class RecordingDb implements IDatabase {
  readonly dbType = "mysql" as const;
  readonly dialect = mysqlDialect;
  calls: Array<{ sql: string; params: unknown[] }> = [];

  constructor(private readonly handler: (
    sql: string,
    params: unknown[],
  ) => Record<string, unknown>[]) {}

  async query<T = Record<string, unknown>>(sql: string, params: unknown[] = []): Promise<T[]> {
    this.calls.push({ sql, params });
    return this.handler(sql, params) as T[];
  }

  async exec(sql: string, params: unknown[] = []): Promise<ExecResult> {
    this.calls.push({ sql, params });
    return { affectedRows: 1 };
  }
  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> { return fn(this); }
  async close(): Promise<void> { return undefined; }
}

describe("EvolveRepository collaborative Bot access", () => {
  it("writes task log BIGINT timestamps as unix seconds on MySQL/ZDAS", async () => {
    const row = {
      id: 1, archive_id: "LOG-1", task_id: "EV-1", active_key: "EV-1", status: "dispatching",
      requested_by: "owner-1", transport: null, bot_run_id: null, bot_session_id: null,
      platform_response_json: null, artifact_ref: null, artifact_size: null, artifact_sha256: null,
      artifact_content_type: null, metadata_json: null, error_code: null, error_message: null,
      started_at: null, completed_at: null, gmt_create: 1, gmt_modified: 1,
    };
    const db = new RecordingDb((sql) => sql.includes("FROM ce_task_log_archives") ? [row] : []);

    await new EvolveRepository(db).createTaskLogArchive({
      archiveId: "LOG-1", taskId: "EV-1", requestedBy: "owner-1",
    });

    const insert = db.calls.find((call) => call.sql.includes("INSERT INTO ce_task_log_archives"));
    expect(insert?.params.slice(-2).every((value) => typeof value === "number")).toBe(true);
  });

  it("merges owned and collaborated Bots and marks their access type", async () => {
    const db = new RecordingDb((sql) => {
      if (sql.includes("FROM ac_bot_collaborator")) return [{
        bot_id: "service-bot", bot_name: "协作服务 Bot", env: "prod",
        active_engine: "openclaw", bot_type: "service", owner_id: "owner-2",
      }];
      if (sql.includes("FROM ac_bots")) return [{
        id: 1, bot_id: "owned-bot", bot_name: "我的 Bot", env: "prod",
        active_engine: "openclaw", bot_type: "draft",
      }];
      return [];
    });

    const bots = await new EvolveRepository(db).listAccessibleEvolveBots("collaborator-1");

    expect(bots).toEqual([
      expect.objectContaining({ botId: "owned-bot", ownerId: "collaborator-1", accessType: "owner" }),
      expect.objectContaining({ botId: "service-bot", ownerId: "owner-2", accessType: "collaborator" }),
    ]);
  });

  it("keeps pre and prod runtime instances for the same owned Bot", async () => {
    const db = new RecordingDb((sql) => {
      if (sql.includes("FROM ac_bot_collaborator")) return [];
      if (sql.includes("FROM ac_bots")) return [
        { id: 1, bot_id: "same-bot", bot_name: "预发", env: "pre", active_engine: "openclaw", bot_type: "draft" },
        { id: 2, bot_id: "same-bot", bot_name: "生产", env: "prod", active_engine: "openclaw", bot_type: "draft" },
      ];
      return [];
    });

    const bots = await new EvolveRepository(db).listAccessibleEvolveBots("owner-1");

    expect(bots.map((bot) => `${bot.ownerId}/${bot.botId}/${bot.env}`)).toEqual([
      "owner-1/same-bot/pre",
      "owner-1/same-bot/prod",
    ]);
  });

  it("resolves a collaborated Bot using its real owner for the runtime lookup", async () => {
    const db = new RecordingDb((sql, params) => {
      if (sql.includes("SELECT b.active_engine") && params[1] === "collaborator-1") return [];
      if (sql.includes("SELECT c.owner_id")) return [{ owner_id: "owner-2" }];
      if (sql.includes("SELECT b.active_engine") && params[1] === "owner-2") return [{
        active_engine: "openclaw", bot_type: "service", bot_status: "active", binding_id: 7,
        device_provider: "BAAS", device_id: "device-1", binding_status: "active", env: "prod",
      }];
      if (sql.includes("COUNT(*) AS published_count")) return [{ published_count: 1 }];
      return [];
    });

    const resolved = await new EvolveRepository(db)
      .resolveAccessibleEvolveBotRuntime("collaborator-1", "service-bot");

    expect(resolved).toEqual(expect.objectContaining({
      ownerId: "owner-2", accessType: "collaborator",
      runtime: expect.objectContaining({ botType: "service", hasServiceBot: true }),
    }));
    expect(db.calls.some((call) => call.params[1] === "owner-2")).toBe(true);
  });

  it("exposes the actual owner on the generic runtime used by all Evolve tasks", async () => {
    const db = new RecordingDb((sql, params) => {
      if (sql.includes("SELECT b.active_engine") && params[1] === "collaborator-1") return [];
      if (sql.includes("SELECT c.owner_id")) return [{ owner_id: "owner-2" }];
      if (sql.includes("SELECT b.active_engine") && params[1] === "owner-2") return [{
        active_engine: "openclaw", bot_type: "service", bot_status: "active", binding_id: 7,
        device_provider: "BAAS", device_id: "device-1", binding_status: "active", env: "prod",
      }];
      if (sql.includes("COUNT(*) AS published_count")) return [{ published_count: 1 }];
      return [];
    });

    const runtime = await new EvolveRepository(db)
      .resolveEvolveBotRuntime("collaborator-1", "service-bot");

    expect(runtime).toEqual(expect.objectContaining({
      ownerId: "owner-2", accessType: "collaborator", botType: "service",
    }));
  });
});
