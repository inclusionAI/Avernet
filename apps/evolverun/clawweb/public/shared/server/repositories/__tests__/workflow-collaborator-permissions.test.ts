// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase } from "../../db.js";
import type { DirectoryBot } from "../../services/bot-directory.js";
import { BotWorkflowPermissionRepository } from "../bot-workflow-permission-repository.js";

const databases: Database.Database[] = [];
afterEach(() => { databases.splice(0).forEach(db => db.close()); vi.restoreAllMocks(); });

function fixture() {
  const raw = new Database(":memory:");
  databases.push(raw);
  raw.exec(`CREATE TABLE bot_workflow_permissions (
    id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL, env TEXT NOT NULL,
    can_view INTEGER NOT NULL, can_execute INTEGER NOT NULL, can_edit INTEGER NOT NULL,
    gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
  )`);
  const bots: DirectoryBot[] = [{
    botId: "shared-bot", ownerId: "owner", accessType: "collaborator",
    displayBotId: "shared-bot", status: "active", source: "test",
  }];
  const directory = { listBots: vi.fn(async (actor: string) => actor === "member" ? bots : []) };
  const repo = new BotWorkflowPermissionRepository(new SqliteDatabase(raw), directory);
  const grant = (workflow: string, owner = "owner", bot: string | null = "shared-bot", view = 1, edit = 1) =>
    repo.upsert({ workflow_id: workflow, bot_owner_id: owner, bot_id: bot,
      can_view: view, can_edit: edit, can_execute: 0 });
  return { raw, repo, directory, bots, grant };
}

describe("workflow collaborator permission union", () => {
  it("inherits exact Bot grants for viewing and editing, with Bot-scoped run visibility", async () => {
    const { repo, grant } = fixture();
    await grant("shared");
    expect((await repo.getViewByIdsForOwner("member"))?.viewableIds.has("shared")).toBe(true);
    expect(await repo.hasEditPermission("shared", "member")).toBe(true);
    expect(await repo.resolveViewScope("shared", "member")).toEqual({ botIds: ["shared-bot"] });
    expect(await repo.resolveRunViewScope("shared", "member")).toEqual({ bots: [{ botId: "shared-bot", ownerId: "owner" }] });
    expect(await repo.hasEditPermission("shared", "stranger")).toBe(false);
  });

  it("unions independent user grants and Bot grants without a zero overriding a one", async () => {
    const { repo, grant } = fixture();
    await grant("shared", "owner", "shared-bot", 0, 1);
    await grant("shared", "member", null, 1, 0);
    await grant("personal", "member", null, 1, 1);
    const view = await repo.getViewByIdsForOwner("member");
    expect([...view!.viewableIds].sort()).toEqual(["personal", "shared"]);
    expect(await repo.hasEditPermission("shared", "member")).toBe(true);
    expect(await repo.resolveViewScope("shared", "member")).toBe("all");
  });

  it("does not inherit an Owner's private, wildcard, other-Bot, or mismatched-Owner grants", async () => {
    const { repo, grant } = fixture();
    await grant("private", "owner", null);
    await grant("owner-wide", "owner", "*");
    await grant("other-bot", "owner", "other-bot");
    await grant("other-owner", "other-owner", "shared-bot");
    expect((await repo.getViewByIdsForOwner("member"))?.viewableIds.size).toBe(0);
    for (const workflow of ["private", "owner-wide", "other-bot", "other-owner"]) {
      expect(await repo.hasEditPermission(workflow, "member")).toBe(false);
      expect(await repo.resolveViewScope(workflow, "member")).toBe("deny");
    }
  });

  it("keeps view-only Bot grants view-only and respects denied Bot grants", async () => {
    const { repo, grant } = fixture();
    await grant("view-only", "owner", "shared-bot", 1, 0);
    await grant("denied", "owner", "shared-bot", 0, 0);
    expect([...(await repo.getViewByIdsForOwner("member"))!.viewableIds]).toEqual(["view-only"]);
    expect(await repo.hasEditPermission("view-only", "member")).toBe(false);
    expect(await repo.resolveViewScope("denied", "member")).toBe("deny");
  });

  it("revokes inherited permissions immediately while retaining direct user grants", async () => {
    const { repo, grant, bots } = fixture();
    await grant("shared");
    await grant("personal", "member", null);
    expect(await repo.hasEditPermission("shared", "member")).toBe(true);
    bots.splice(0);
    expect(await repo.hasEditPermission("shared", "member")).toBe(false);
    expect([...(await repo.getViewByIdsForOwner("member"))!.viewableIds]).toEqual(["personal"]);
    expect(await repo.resolveViewScope("shared", "member")).toBe("deny");
  });

  it("falls back to direct grants on directory failure without retaining stale inherited access", async () => {
    const { repo, grant, directory } = fixture();
    await grant("shared");
    await grant("personal", "member", null);
    expect(await repo.hasEditPermission("shared", "member")).toBe(true);
    directory.listBots.mockRejectedValue(new Error("directory unavailable"));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect([...(await repo.getViewByIdsForOwner("member"))!.viewableIds]).toEqual(["personal"]);
    expect(await repo.hasEditPermission("shared", "member")).toBe(false);
    expect(await repo.hasEditPermission("personal", "member")).toBe(true);
    expect(await repo.resolveViewScope("shared", "member")).toBe("deny");
    expect(warning).toHaveBeenCalled();
  });

  it("does not turn permission database errors into successful fallback", async () => {
    const { repo, raw } = fixture();
    raw.exec("DROP TABLE bot_workflow_permissions");
    await expect(repo.hasEditPermission("shared", "member")).rejects.toThrow();
  });

  it("does not inherit collaboration into bot-runtime permission checks", async () => {
    const { repo, grant, directory } = fixture();
    await grant("shared");
    expect(await repo.hasEditPermission("shared", "member", "shared-bot")).toBe(false);
    expect((await repo.getViewByIdsForOwner("member", "shared-bot"))?.viewableIds.size).toBe(0);
    expect(await repo.checkPermission("shared-bot", "member", "shared", "edit")).toBe(false);
    expect(await repo.checkPermission("shared-bot", "owner", "shared", "edit")).toBe(true);
    expect(directory.listBots).not.toHaveBeenCalled();
  });

  it("ignores incomplete or wildcard Bot identities from the directory", async () => {
    const { repo, grant, bots } = fixture();
    await grant("shared");
    bots[0].ownerId = null;
    expect(await repo.hasEditPermission("shared", "member")).toBe(false);
    bots[0].ownerId = "*";
    expect(await repo.hasEditPermission("shared", "member")).toBe(false);
    bots[0].ownerId = "owner";
    bots[0].botId = "*";
    expect(await repo.hasEditPermission("shared", "member")).toBe(false);
  });
});
