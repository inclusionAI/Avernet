// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Request, Response, Router } from "express";
import type { BotWorkflowPermissionRepository, BotWorkflowPermissionRow } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createBotWorkflowPermissionCloneRouter, type CloneRouterDeps } from "../bot-workflow-permission-clone.js";

const listRowsForBot = vi.fn();
const listOwnerLevelRows = vi.fn();
const upsert = vi.fn();
const resolveBotOwner = vi.fn();

function makeRepo(): BotWorkflowPermissionRepository {
  return { listRowsForBot, listOwnerLevelRows, upsert } as unknown as BotWorkflowPermissionRepository;
}

function makeDeps(overrides: Partial<CloneRouterDeps> = {}): CloneRouterDeps {
  return {
    botPermRepo: makeRepo(),
    resolveBotOwner,
    ...overrides,
  };
}

// Exercise the actual Express router without sockets or HTTP (auto-heal-run pattern).
function request(
  router: Router,
  payload: unknown,
): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const headers = { "content-type": "application/json" };
    const req = { method: "POST", url: "/", headers, body: payload } as Request;
    let status = 200;
    const res = {
      status(code: number) { status = code; return this; },
      json(data: Record<string, unknown>) { resolve({ status, body: data }); return this; },
    } as Response;
    router(req, res, (error?: unknown) => (error ? reject(error) : resolve({ status: 404, body: {} })));
  });
}

function row(workflowId: string, grant: [number, number, number], botId = "b1", ownerId = "o1"): BotWorkflowPermissionRow {
  return {
    id: 1, bot_id: botId, bot_owner_id: ownerId, workflow_id: workflowId, env: "prod",
    can_view: grant[0], can_execute: grant[1], can_edit: grant[2],
    gmt_create: new Date(), gmt_modified: new Date(),
  } as BotWorkflowPermissionRow;
}

/** Route source/target reads by (owner, bot) so a single mock serves both sides. */
function stubReads(sourceRows: BotWorkflowPermissionRow[], targetRows: BotWorkflowPermissionRow[]) {
  listRowsForBot.mockImplementation(async (ownerId: string, botId: string) =>
    ownerId === "src-owner" && botId === "src-bot" ? sourceRows
      : ownerId === "tgt-owner" && botId === "tgt-bot" ? targetRows
      : []);
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe("POST / (bot workflow permission clone)", () => {
  it("returns 503 when the permission repository is not configured", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter({ botPermRepo: null }), {});
    expect(result).toMatchObject({ status: 503, body: { code: "SERVICE_UNAVAILABLE" } });
  });

  it("returns 400 when source_bot_id is missing", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });
    expect(result).toMatchObject({ status: 400, body: { code: "INVALID_PARAMS" } });
  });

  it("returns 400 when target_bot_id is missing", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner" });
    expect(result).toMatchObject({ status: 400, body: { code: "INVALID_PARAMS" } });
  });

  it("returns 400 for a bare bot id with no owner param, compound form, or resolver", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps({ resolveBotOwner: null })),
      { source_bot_id: "src-bot", target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });
    expect(result).toMatchObject({ status: 400, body: { code: "INVALID_PARAMS" } });
  });

  it("returns 404 BOT_NOT_FOUND when the owner lookup cannot resolve the bot", async () => {
    resolveBotOwner.mockResolvedValue(null);
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });
    expect(result).toMatchObject({ status: 404, body: { code: "BOT_NOT_FOUND" } });
  });

  it("returns 400 for an invalid scope", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner", scope: "everything" });
    expect(result).toMatchObject({ status: 400, body: { code: "INVALID_PARAMS" } });
  });

  it("returns 409 when source and target resolve to the same identity", async () => {
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "default:390565", target_bot_id: "default", target_owner_id: "390565" });
    expect(result).toMatchObject({ status: 409, body: { code: "IDENTITY_CONFLICT" } });
  });

  it("returns 404 NO_SOURCE_GRANTS when the source bot has no permission rows", async () => {
    stubReads([], []);
    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });
    expect(result).toMatchObject({ status: 404, body: { code: "NO_SOURCE_GRANTS" } });
  });

  it("clones exact rows and resolves identity through the owner lookup", async () => {
    resolveBotOwner.mockImplementation(async (botId: string) => (botId === "src-bot" ? "src-owner" : "tgt-owner"));
    stubReads(
      [row("w1", [1, 1, 1]), row("w2", [1, 1, 0])],
      [],
    );
    upsert.mockImplementation(async (data: { workflow_id: string; can_view: number; can_execute: number; can_edit: number }) =>
      row(data.workflow_id, [data.can_view, data.can_execute, data.can_edit], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", target_bot_id: "tgt-bot" });

    expect(result.status).toBe(200);
    expect(result.body).toMatchObject({ success: true, granted: 2, dry_run: false });
    expect(result.body.source_resolved).toMatchObject({ bot_id: "src-bot", owner_id: "src-owner", source: "directory" });
    expect(result.body.target_resolved).toMatchObject({ bot_id: "tgt-bot", owner_id: "tgt-owner", source: "directory" });
    expect(upsert).toHaveBeenCalledTimes(2);
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      bot_id: "tgt-bot", bot_owner_id: "tgt-owner", workflow_id: "w1",
      can_view: 1, can_execute: 1, can_edit: 1,
    }));
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      bot_id: "tgt-bot", bot_owner_id: "tgt-owner", workflow_id: "w2",
      can_view: 1, can_execute: 1, can_edit: 0,
    }));
  });

  it("resolves the compound BaaS form bot_id:owner", async () => {
    listRowsForBot.mockImplementation(async (ownerId: string, botId: string) =>
      ownerId === "355215" && botId === "default" ? [row("w1", [1, 0, 0], "default", "355215")] : []);
    upsert.mockResolvedValue(row("w1", [1, 0, 0], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "default:355215", target_bot_id: "20260923_2kya0jax:103892" });

    expect(result.status).toBe(200);
    expect(listRowsForBot).toHaveBeenCalledWith("355215", "default");
    expect(result.body.source_resolved).toMatchObject({ bot_id: "default", owner_id: "355215", source: "compound" });
    expect(result.body.target_resolved).toMatchObject({ bot_id: "20260923_2kya0jax", owner_id: "103892", source: "compound" });
  });

  it("explicit owner param wins over the compound suffix", async () => {
    listRowsForBot.mockImplementation(async (ownerId: string, botId: string) =>
      ownerId === "real-owner" && botId === "src-bot" ? [row("w1", [1, 0, 0], "src-bot", "real-owner")] : []);
    upsert.mockResolvedValue(row("w1", [1, 0, 0], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot:not-this-owner", source_owner_id: "real-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });

    expect(result.status).toBe(200);
    expect(listRowsForBot).toHaveBeenCalledWith("real-owner", "src-bot");
    expect(result.body.source_resolved).toMatchObject({ source: "param" });
  });

  it("materializes owner-level rows only under scope=exact+owner-rows", async () => {
    stubReads([row("w1", [1, 1, 1])], []);
    listOwnerLevelRows.mockResolvedValue([
      { ...row("w2", [1, 1, 0], null, "src-owner"), bot_id: null },
      { ...row("w3", [1, 0, 0], "*", "src-owner") },
    ]);
    upsert.mockImplementation(async (data: { workflow_id: string }) =>
      row(data.workflow_id, [1, 1, 1], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner", scope: "exact+owner-rows" });

    expect(result.status).toBe(200);
    expect(listOwnerLevelRows).toHaveBeenCalledWith("src-owner");
    expect(result.body).toMatchObject({ granted: 3, source_effective_total: 3 });
    expect(upsert).toHaveBeenCalledTimes(3);
  });

  it("does not read owner-level rows under the default scope", async () => {
    stubReads([row("w1", [1, 1, 1])], []);
    upsert.mockResolvedValue(row("w1", [1, 1, 1], "tgt-bot", "tgt-owner"));

    await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });

    expect(listOwnerLevelRows).not.toHaveBeenCalled();
  });

  it("skips workflows the target already covers (union never downgrades)", async () => {
    stubReads(
      [row("w1", [1, 1, 0])],
      [row("w1", [1, 1, 1], "tgt-bot", "tgt-owner")],
    );

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });

    expect(result.status).toBe(200);
    expect(upsert).not.toHaveBeenCalled();
    expect(result.body).toMatchObject({ granted: 0, skipped: [{ workflow_id: "w1", reason: "already-covered" }] });
  });

  it("upgrades a weaker existing target row instead of skipping it", async () => {
    stubReads(
      [row("w1", [1, 1, 1])],
      [row("w1", [1, 0, 0], "tgt-bot", "tgt-owner")],
    );
    upsert.mockResolvedValue(row("w1", [1, 1, 1], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });

    expect(result.status).toBe(200);
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      workflow_id: "w1", can_view: 1, can_execute: 1, can_edit: 1,
    }));
    expect(result.body).toMatchObject({ granted: 1 });
  });

  it("dry_run reports the would-be grants without writing", async () => {
    stubReads([row("w1", [1, 1, 1]), row("w2", [1, 0, 0])], []);

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner", dry_run: true });

    expect(result.status).toBe(200);
    expect(result.body).toMatchObject({ dry_run: true, granted: 2 });
    expect(upsert).not.toHaveBeenCalled();
    expect(result.body.rows).toEqual([
      { workflow_id: "w1", can_view: 1, can_execute: 1, can_edit: 1 },
      { workflow_id: "w2", can_view: 1, can_execute: 0, can_edit: 0 },
    ]);
  });

  it("merges duplicate rows for one workflow (multi-env reads) with widest-rule-wins", async () => {
    stubReads([
      row("w1", [1, 1, 0]),
      { ...row("w1", [0, 0, 1]) },
    ], []);
    upsert.mockResolvedValue(row("w1", [1, 1, 1], "tgt-bot", "tgt-owner"));

    const result = await request(createBotWorkflowPermissionCloneRouter(makeDeps()),
      { source_bot_id: "src-bot", source_owner_id: "src-owner",
        target_bot_id: "tgt-bot", target_owner_id: "tgt-owner" });

    expect(result.status).toBe(200);
    expect(upsert).toHaveBeenCalledTimes(1);
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      workflow_id: "w1", can_view: 1, can_execute: 1, can_edit: 1,
    }));
    expect(result.body).toMatchObject({ granted: 1, source_effective_total: 1 });
  });
});