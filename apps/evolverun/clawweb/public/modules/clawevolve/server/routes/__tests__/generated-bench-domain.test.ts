import { beforeAll, afterAll, describe, it, expect, vi } from "vitest";
import express from "express";
import type { Server } from "node:http";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { BenchDomainRepository } from "../../repositories/bench-domain-repository.js";
import { createBenchRouter } from "../bench.js";
import { registerGeneratedBenchDomainRoute } from "../generated-bench-domain.js";

describe("generated Bench Domain context", () => {
  let server: Server;
  let url: string;
  let db: SqliteDatabase;
  let domains: BenchDomainRepository;
  const resolveBotSpace = vi.fn().mockResolvedValue("42");

  beforeAll(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    domains = new BenchDomainRepository(db);
    const tasks = new EvolveRepository(db);
    for (const [taskId, entryPoint] of [["EV-new", "teamclaw"], ["EV-old", undefined]]) {
      await tasks.createTask({ taskId, taskType: "full", userId: "owner", botId: "bot",
        taskName: taskId, configJson: JSON.stringify({ botEnv: "pre", entryPoint }), createdBy: "creator" });
      await tasks.createStep({ taskId, stepId: `STEP-${taskId}`, stepType: "plan", stepNo: 1, command: "plan" });
    }
    const app = express();
    app.use(express.json());
    const router = express.Router();
    registerGeneratedBenchDomainRoute(router, { domains, tasks, resolveBotSpace });
    router.use(createBenchRouter(domains, null, null, null, null, db));
    app.use("/api/bench", router);
    app.use(((error, _req, res, _next) => {
      res.status(error.status ?? 500).json({ error: "Request failed" });
    }) as express.ErrorRequestHandler);
    await new Promise<void>((resolve) => { server = app.listen(0, "127.0.0.1", resolve); });
    url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/bench/domains`;
  });
  afterAll(async () => {
    if (server) await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    await db.close();
  });
  const spaceOf = async (domainId: string) => (await db.query<{ space_id: string | null }>(
    "SELECT space_id FROM cm_bench_domains WHERE owner_user_id = ? AND domain_id = ?", ["owner", domainId],
  ))[0]?.space_id;
  const post = (body: Record<string, unknown>, actor = "owner") => fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json", ...(actor ? { "X-User-Id": actor } : {}) },
    body: JSON.stringify(body),
  });
  const source = { sourceTaskId: "EV-new", sourceStepId: "STEP-EV-new" };

  it("adds exactly one business column and no table", async () => {
    const columns = await db.query<{ name: string }>("PRAGMA table_info(cm_bench_domains)");
    expect(columns.map((c) => c.name).sort()).toEqual([
      "id", "domain_id", "name", "description", "status", "created_by", "owner_user_id",
      "gmt_create", "gmt_modified", "source_root", "space_id",
    ].sort());
  });
  it("keeps no-context requests personal, ignoring claimed space/entry point", async () => {
    const response = await post({ domainId: "legacy", name: "legacy", space_id: "42", entryPoint: "teamclaw" });
    expect(response.status).toBe(201);
    expect(await spaceOf("legacy")).toBeNull();
  });
  it("keeps existing task calls personal even when the upgraded Skill sends context", async () => {
    const response = await post({ domainId: "old-task", name: "old-task",
      sourceTaskId: "EV-old", sourceStepId: "STEP-EV-old" });
    expect(response.status).toBe(201);
    expect(await spaceOf("old-task")).toBeNull();
  });
  it("binds only the authoritative Bot space, never the request's space", async () => {
    const response = await post({ domainId: "generated", name: "generated", ...source, spaceId: "evil" });
    expect(response.status).toBe(201);
    expect(await spaceOf("generated")).toBe("42");
    expect(resolveBotSpace).toHaveBeenCalledWith({ ownerId: "owner", botId: "bot", environment: "pre", identity: { userId: "owner" } });
  });
  it("does not reassign a registered Domain when the Bot moves or a task retries", async () => {
    resolveBotSpace.mockResolvedValueOnce("99");
    const count = resolveBotSpace.mock.calls.length;
    expect((await post({ domainId: "generated", name: "generated", ...source })).status).toBe(409);
    expect(resolveBotSpace.mock.calls.length).toBe(count);
    expect(await spaceOf("generated")).toBe("42");
    resolveBotSpace.mockReset().mockResolvedValue("42");
  });
  it("rejects incomplete, foreign, nonexistent, or mismatched context", async () => {
    const body = { domainId: "invalid", name: "invalid", ...source };
    expect((await post({ ...body, sourceStepId: undefined })).status).toBe(400);
    expect((await post(body, "")).status).toBe(401);
    expect((await post(body, "creator")).status).toBe(404);
    expect((await post({ ...body, sourceStepId: "STEP-EV-old" })).status).toBe(404);
    expect((await post({ ...body, sourceTaskId: "missing" })).status).toBe(404);
    expect(await domains.findByOwnerAndDomainId("owner", "invalid")).toBeNull();
  });
  it("does not create a personal fallback for an incomplete Host result", async () => {
    resolveBotSpace.mockResolvedValueOnce("");
    expect((await post({ domainId: "empty-space", name: "empty-space", ...source })).status).toBe(503);
    expect(await domains.findByOwnerAndDomainId("owner", "empty-space")).toBeNull();
  });
  it("does not create a personal fallback when space resolution fails", async () => {
    resolveBotSpace.mockRejectedValueOnce(Object.assign(new Error("unavailable"), { status: 503 }));
    expect((await post({ domainId: "unavailable", name: "unavailable", ...source })).status).toBe(503);
    expect(await domains.findByOwnerAndDomainId("owner", "unavailable")).toBeNull();
  });
  it("keeps legacy creation working before the managed database applies the new DDL", async () => {
    await db.exec("ALTER TABLE cm_bench_domains DROP COLUMN space_id");
    expect((await post({ domainId: "no-ddl-legacy", name: "legacy" })).status).toBe(201);
    expect((await post({ domainId: "no-ddl-new", name: "new", ...source })).status).toBe(500);
    expect(await domains.findByOwnerAndDomainId("owner", "no-ddl-new")).toBeNull();
  });

  it("preserves old task uploads for a linked extension Step without consulting spaces",async()=>{
    const tasks=new EvolveRepository(db);
    await tasks.createStep({taskId:"EV-old",stepId:"old-extension",stepType:"stage_extension",stepNo:2,command:"plan"});
    const response=await post({domainId:"old-extension-domain",name:"Old extension",sourceTaskId:"EV-old",sourceStepId:"old-extension"});
    expect(response.status).toBe(201);expect((await domains.findByOwnerAndDomainId("owner","old-extension-domain"))?.owner_user_id).toBe("owner");
  });

});
