// @vitest-environment node
import { describe, expect, it } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase } from "../../db.js";
import { BotWorkflowPermissionRepository } from "../bot-workflow-permission-repository.js";

describe("BotWorkflowPermissionRepository", () => {
  function createRepo() {
    const raw = new Database(":memory:");
    raw.exec(`
      CREATE TABLE bot_workflow_permissions (
        id INTEGER PRIMARY KEY,
        bot_id TEXT,
        bot_owner_id TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        env TEXT NOT NULL,
        can_view INTEGER NOT NULL,
        can_execute INTEGER NOT NULL,
        can_edit INTEGER NOT NULL,
        gmt_create INTEGER NOT NULL,
        gmt_modified INTEGER NOT NULL
      );
    `);
    const db = new SqliteDatabase(raw);
    return { repo: new BotWorkflowPermissionRepository(db), db, raw };
  }

  describe("resolveViewScope", () => {
    it("returns 'all' when bot_id='*' and bot_owner_id='*' and can_view=1", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "*", bot_owner_id: "*", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "anyone")).toBe("all");
    });

    it("returns 'all' when user has bot_id='*' with can_view=1", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "*", bot_owner_id: "272654", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "272654")).toBe("all");
      expect(await repo.resolveViewScope("wf", "other")).toBe("deny");
    });

    it("returns 'all' when user has NULL bot_id with can_view=1", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: null, bot_owner_id: "272654", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "272654")).toBe("all");
    });

    it("returns allowed botIds for all-users-specific-bot records", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "*", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "anyone")).toEqual({ botIds: ["botA"] });
    });

    it("returns allowed botIds for user-specific-bot records", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "160855")).toEqual({ botIds: ["botA"] });
      expect(await repo.resolveViewScope("wf", "other")).toBe("deny");
    });

    it("merges multiple specific-bot permissions for the same workflow", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      await repo.upsert({ bot_id: "botB", bot_owner_id: "*", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      const scope = await repo.resolveViewScope("wf", "160855");
      expect(scope).toEqual({ botIds: expect.arrayContaining(["botA", "botB"]) });
      expect((scope as { botIds: string[] }).botIds).toHaveLength(2);
    });

    it("returns 'all' when all-bots permission coexists with specific-bot permissions", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      await repo.upsert({ bot_id: "*", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      expect(await repo.resolveViewScope("wf", "160855")).toBe("all");
    });

    it("ignores can_view=0 records", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "160855", workflow_id: "wf", can_view: 0, can_execute: 0, can_edit: 1 });
      expect(await repo.resolveViewScope("wf", "160855")).toBe("deny");
    });

    it("returns 'deny' when no permission records exist", async () => {
      const { repo } = createRepo();
      expect(await repo.resolveViewScope("wf", "160855")).toBe("deny");
    });
  });

  describe("getViewByIdsForOwner", () => {
    it("returns null when no permission records exist", async () => {
      const { repo } = createRepo();
      expect(await repo.getViewByIdsForOwner("160855")).toBeNull();
    });

    it("includes workflows with bot_id='*' and bot_owner_id='*' for any owner", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "*", bot_owner_id: "*", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      const result = await repo.getViewByIdsForOwner("anyone");
      expect(result).not.toBeNull();
      expect(result!.restrictedIds.has("wf")).toBe(true);
      expect(result!.viewableIds.has("wf")).toBe(true);
    });

    it("includes workflows with owner-level all-bots permission", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "*", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      const result = await repo.getViewByIdsForOwner("160855");
      expect(result!.viewableIds.has("wf")).toBe(true);
    });

    it("when botId is provided, only matches viewable specific-bot or owner-all-bots records", async () => {
      const { repo } = createRepo();
      await repo.upsert({ bot_id: "botA", bot_owner_id: "160855", workflow_id: "wf", can_view: 1, can_execute: 0, can_edit: 0 });
      await repo.upsert({ bot_id: "botB", bot_owner_id: "160855", workflow_id: "wf2", can_view: 1, can_execute: 0, can_edit: 0 });
      const withBotA = await repo.getViewByIdsForOwner("160855", "botA");
      expect(withBotA!.viewableIds.has("wf")).toBe(true);
      expect(withBotA!.viewableIds.has("wf2")).toBe(false);

      const withBotB = await repo.getViewByIdsForOwner("160855", "botB");
      expect(withBotB!.viewableIds.has("wf2")).toBe(true);
      expect(withBotB!.viewableIds.has("wf")).toBe(false);
    });
  });
});
