// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import express from "express";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { createHash, createHmac, randomUUID as uuid } from "node:crypto";
import type { Server } from "node:http";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { createInsightManagementRuntime } from "../../services/insight/management/runtime.js";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";

const secret = uuid();
let db: SqliteDatabase, server: Server, base: string;
const machine = "/internal/governance/improvement-management";
const admin = "/admin/improvement-management";
async function seed(status = "ACTIVE", sourceType = "ADMIN_RULE_DIRECT_EVOLUTION") {
  const { item } = await new InsightImprovementRepository(db).create({
    ownerUserId: "owner-1", botOwnerUserId: "owner-1", botId: "bot-1", title: "Old issue",
    userGuidance: "Keep this original evidence", sourceType: sourceType as "ADMIN_RULE_DIRECT_EVOLUTION", sourceRuleId: "rule-1",
    assignmentReason: "owner", rootCauseSummary: "Original failure", suggestedAction: "Original suggestion",
    evidence: [], dataStartTime: null, dataEndTime: null, dataAsOf: "2026-08-01", batchId: "test",
    contentFingerprint: uuid(), idempotencyKey: uuid(), initialStatus: status as "ACTIVE", createdBy: "seed",
  });
  return item.id;
}
function signed(path: string, body: unknown, method = "POST", client = "manager") {
  const stamp = String(Date.now()), nonce = uuid();
  const digest = createHash("sha256").update(method === "GET" ? "" : JSON.stringify(body ?? {})).digest("hex");
  const signature = createHmac("sha256", secret).update([method, path, stamp, nonce, digest].join("\n")).digest("hex");
  return { "Content-Type": "application/json", "X-Agent-Id": client, "X-Agent-Timestamp": stamp,
    "X-Agent-Nonce": nonce, "X-Agent-Body-SHA256": digest, "X-Agent-Signature": signature };
}
async function call(id: number, action: string, version = 1, extra = {}) {
  const path = `${machine}/${id}`;
  const body = { requestId: uuid(), action, version, reason: "Operator decision, not verified repair", ...extra };
  const response = await fetch(base + path, { method: "POST", headers: signed(path, body), body: JSON.stringify(body) });
  return { status: response.status, data: await response.json(), body };
}
beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite");
  const runtime = createInsightManagementRuntime(db, { INSIGHT_AGENT_CLIENTS_JSON: JSON.stringify({
    manager: { secret, scopes: ["improvement.manage"] }, reader: { secret, scopes: ["rule.read"] },
  }) });
  const app = express(); app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = req.get("X-User-Id") === "admin-1"; next(); });
  app.use("/api/insight/v1", runtime.router);
  server = await new Promise(resolve => { const s = app.listen(0, () => resolve(s)); });
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/insight/v1`;
});
afterEach(async () => { await new Promise<void>((resolve, reject) => server.close(e => e ? reject(e) : resolve())); await db.close(); });

describe("authorized management without verification workflow gates", () => {
  it.each(["ACTIVE", "IN_PROGRESS", "RESOLVED", "ARCHIVED"])("closes %s without sessions, handledAt or waiting", async state => {
    const id = await seed(state); const r = await call(id, "close"); expect(r.status, JSON.stringify(r.data)).toBe(200);
    expect(r.data.improvement).toMatchObject({ status: "RESOLVED", version: 2, ownerUserId: "owner-1", botId: "bot-1",
      verificationStatus: "INSUFFICIENT_DATA", verificationNewSessionCount: 0, resolvedSource: "MANUAL_CLOSED" });
    expect(r.data.receipt.actor).toEqual({ kind: "agent", id: "manager" });
    const reread = await new InsightImprovementRepository(db).getDetailById(id);
    expect(reread).toMatchObject(r.data.improvement);
    expect(reread?.userGuidance).toContain("Original failure");
    expect(await new InsightImprovementRepository(db).getAutoClosureRateByDate({ userId: "*" })).toEqual({});
  });
  it("supports reopen, start, defer, assign owner, reject, edit and close in one contract", async () => {
    const id = await seed("RESOLVED"); let version = 1;
    for (const [action, status] of [["reopen", "ACTIVE"], ["start", "IN_PROGRESS"], ["defer", "ACTIVE"], ["assign_owner", "ACTIVE"], ["reject", "ARCHIVED"], ["reopen", "ACTIVE"], ["edit", "ACTIVE"], ["close", "RESOLVED"]]) {
      const r = await call(id, action, version, action === "edit" ? { title: "Updated", suggestedAction: "New direction", note: "Keep audit" } : {});
      expect(r.status, JSON.stringify(r.data)).toBe(200); expect(r.data.improvement.status).toBe(status); version++;
      expect(r.data.improvement.version).toBe(version);
    }
    const item = await new InsightImprovementRepository(db).getDetailById(id);
    expect(item).toMatchObject({ title: "Updated", suggestedAction: "New direction", actionType: "ASSIGN_OWNER" });
    expect(item?.userGuidance).toContain("Original failure");
  });
  it("replays the same operation after later changes without mutating again", async () => {
    const id = await seed(); const first = await call(id, "close"); await call(id, "reopen", 2);
    const replay = await call(id, "close", 1, first.body);
    expect(replay.status).toBe(200); expect(replay.data.replayed).toBe(true);
    expect(replay.data.receipt.afterVersion).toBe(2); expect(replay.data.improvement).toMatchObject({ status: "ACTIVE", version: 3 });
    expect((await call(id, "close", 3, { requestId: first.body.requestId })).status).toBe(409);
    expect((await call(id, "close", 1)).status).toBe(409);
  });
  it("conflicts on concurrent updates instead of losing history", async () => {
    const id = await seed(); const results = await Promise.all([call(id, "close"), call(id, "start")]);
    expect(results.map(r => r.status).sort()).toEqual([200, 409]);
    expect((await new InsightImprovementRepository(db).getDetailById(id))?.version).toBe(2);
  });
  it("cannot grant approval or change ownership/evidence through management", async () => {
    const id = await seed("PENDING_ADMIN");
    expect((await call(id, "reopen")).data.code).toBe("APPROVAL_REQUIRED");
    expect((await call(id, "close")).status).toBe(409);
    expect((await call(id, "edit", 1, { note: "Review later" })).status).toBe(200);
    expect((await call(id, "edit", 2, { ownerUserId: "attacker", title: "x" })).status).toBe(400);
  });
  it("requires signed scoped machine identity even on localhost", async () => {
    const id = await seed(); const path = `${machine}/${id}`;
    const body = { action: "close", version: 1, requestId: "auth-test", reason: "close" };
    for (const headers of [{ "Content-Type": "application/json" }, signed(path, body, "POST", "reader"), { ...signed(path, body), "X-Agent-Signature": "0".repeat(64) }]) {
      const r = await fetch(base + path, { method: "POST", headers, body: JSON.stringify(body) }); expect(r.status).toBe(401);
    }
    expect((await new InsightImprovementRepository(db).getDetailById(id))?.version).toBe(1);
  });
  it("allows authenticated admin decisions with CSRF protection", async () => {
    const id = await seed(); const body = { action: "close", version: 1, requestId: "admin-close", reason: "retired" };
    const headers = { "Content-Type": "application/json", "X-User-Id": "admin-1" };
    expect((await fetch(`${base}${admin}/${id}`, { method: "POST", headers, body: JSON.stringify(body) })).status).toBe(403);
    const response = await fetch(`${base}${admin}/${id}`, { method: "POST", headers: { ...headers, "X-Insight-Management-CSRF": "1" }, body: JSON.stringify(body) });
    expect(response.status).toBe(200); expect((await response.json()).receipt.actor).toEqual({ kind: "admin", id: "admin-1" });
  });
  it("returns per-item batch outcomes and rejects duplicate targets before writing", async () => {
    const a = await seed(), b = await seed(); const path = `${machine}/batch/execute`;
    const command = { action: "close", version: 1, requestId: "batch", reason: "cleanup" };
    const body = { items: [{ improvementId: a, ...command }, { improvementId: b, ...command, version: 99 }] };
    const r = await fetch(base + path, { method: "POST", headers: signed(path, body), body: JSON.stringify(body) });
    expect(r.status).toBe(200); const result = await r.json(); expect(result.results.map((x: { ok: boolean }) => x.ok)).toEqual([true, false]);
    const duplicate = { items: [{ improvementId: b, ...command }, { improvementId: b, ...command }] };
    expect((await fetch(base + path, { method: "POST", headers: signed(path, duplicate), body: JSON.stringify(duplicate) })).status).toBe(400);
    expect((await new InsightImprovementRepository(db).getDetailById(b))?.version).toBe(1);
  });
  it("exposes discoverable capabilities and readback to signed clients", async () => {
    const id = await seed();
    for (const path of [`${machine}/capabilities`, machine, `${machine}/${id}`]) {
      const response = await fetch(base + path, { headers: signed(path, {}, "GET") }); expect(response.status).toBe(200);
    }
  });
  it("does not count historical VERIFIED text after a management closure", async () => {
    const id = await seed("RESOLVED");
    await db.exec("UPDATE insight_improvement_item SET user_guidance = ?, applied_at = 1 WHERE id = ?",
      ["Original\n\n[自动验证]\n状态：VERIFIED\n检查时间：2026-08-01 10:00:00\n关闭来源：AUTO_VERIFIED", id]);
    const repo = new InsightImprovementRepository(db);
    expect((await repo.getRuleEvolutionStats("rule-1")).successCount).toBe(1);
    const response = await call(id, "close", 1, { reason: "Not an [自动验证] result\n状态：VERIFIED" });
    expect(response.status).toBe(200);
    expect(response.data.improvement.resolvedSource).toBe("MANUAL_CLOSED");
    expect((await repo.getRuleEvolutionStats("rule-1")).successCount).toBe(0);
    expect(await repo.getAutoClosureRateByDate({ userId: "*" })).toEqual({});
    await call(id, "reopen", 2);
    expect((await repo.getDetailById(id))?.verificationStatus).not.toBe("VERIFIED");
  });
  it("keeps management rejection visible to existing feedback consumers", async () => {
    const id = await seed();
    expect((await call(id, "reject")).status).toBe(200);
    const repo = new InsightImprovementRepository(db);
    expect((await repo.listAllRejections({ since: new Date(0), limit: 100, offset: 0 })).items.map(x => x.improvementId)).toContain(id);
    expect((await call(id, "reopen", 2)).data.improvement).toMatchObject({ status: "ACTIVE", rejectedBy: null });
  });
  it("rolls back the update if persistent readback fails", async () => {
    const id = await seed();
    const original = db.transaction.bind(db);
    db.transaction = fn => original(async tx => {
      const exec = tx.exec.bind(tx);
      tx.exec = async (sql, params) => {
        const result = await exec(sql, params);
        if (sql.startsWith("UPDATE insight_improvement_item")) throw new Error("simulated write acknowledgement loss");
        return result;
      };
      return fn(tx);
    });
    const response = await call(id, "close"); expect(response.status).toBe(500);
    expect((await new InsightImprovementRepository(db).getDetailById(id))).toMatchObject({ status: "ACTIVE", version: 1 });
  });
  it("runs the AIStudio Python client against the real signed HTTP contract", async () => {
    const id = await seed(); const dir = await mkdtemp(join(tmpdir(), "insight-manage-"));
    try {
      const env = { ...process.env, INSIGHT_BASE_URL: new URL(base).origin, INSIGHT_AGENT_ID: "manager", INSIGHT_AGENT_SECRET: secret };
      const script = join(process.cwd(), "scripts/insight_manage.py");
      const run = promisify(execFile);
      const cap = await run("python3", [script, "capabilities"], { env });
      expect(JSON.parse(cap.stdout).requiresNewSession).toBe(false);
      const file = join(dir, "command.json");
      await writeFile(file, JSON.stringify({ requestId: "python-close", action: "close", version: 1, reason: "旧项清理，不是验收" }));
      const result = await run("python3", [script, "execute", "--id", String(id), "--file", file], { env });
      expect(JSON.parse(result.stdout).improvement.status).toBe("RESOLVED");
      const replay = await run("python3", [script, "execute", "--id", String(id), "--file", file], { env });
      expect(JSON.parse(replay.stdout).replayed).toBe(true);
    } finally { await rm(dir, { recursive: true, force: true }); }
  });

});
