import { initializeMonitoringSqlite } from "../../services/monitoring/schema.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { request } from "node:http";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { database } from "../../services/monitoring/__tests__/test-database.js";
import { createMonitoringRuntime } from "../../services/monitoring/monitoring-runtime.js";
import { createInsightRouter } from "../insight.js";

const fixture = (name: string) =>
  JSON.parse(
    readFileSync(
      join(process.cwd(), "server/fixtures/monitoring", `${name}.json`),
      "utf8",
    ),
  );
const alert = fixture("alert"), pass = fixture("pass"), unresolved = fixture("unresolved");
const now = Date.parse("2026-09-10T09:00:00Z");
const check = { schemaVersion: "claw-monitoring/bot-check/v1", botId: "mock-bot-te", engine: "TE",
  checkedAt: new Date(now).toISOString(), lastSuccessfulCheckAt: new Date(now).toISOString(), status: "HEALTHY" };
let db: IDatabase, dir: string, url: string;
let server: ReturnType<express.Application["listen"]> | undefined;
let clock: number;
const bots = [{ botId: "mock-bot-te", engine: "TE" }, { botId: "mock-bot-oc", engine: "OC" }, { botId: "empty-bot", engine: "TE" }];
let env: Record<string, string | undefined>;
async function start(parent = true, defaultFactory = false, getDb = () => db) {
  const app = express();
  if (parent) app.use(express.json({ limit: "10mb" }));
  const runtime = createMonitoringRuntime(getDb, env, () => clock);
  app.use("/api/insight/v1", defaultFactory ? createInsightRouter(null) : createInsightRouter(null, { monitoring: runtime }));
  server = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const s = app.listen(0, "127.0.0.1", (error?: Error) => error ? reject(error) : resolve(s));
    s.once("error", reject);
  });
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/insight/v1`;
}
async function stop() {
  if (server) { await new Promise<void>((resolve, reject) => server!.close((error) => error ? reject(error) : resolve())); server = undefined; }
}
async function call(path: string, init?: RequestInit) {
  const response = await fetch(url + path, init);
  const body = await response.json();
  return { status: response.status, body, cache: response.headers.get("cache-control") };
}
async function post(event: Record<string, unknown>, path = "diagnosis-events", headers: Record<string, string> = {}) {
  return call(`/internal/monitoring/${path}`, { method: "POST", headers: {
    "Content-Type": "application/json", ...(path === "diagnosis-events" ? { "Idempotency-Key": String(event.eventId) } : {}), ...headers,
  }, body: JSON.stringify(event) });
}
async function get(bot = "mock-bot-te", query = "") { return call(`/monitoring/bots/${bot}/diagnoses${query}`); }
beforeEach(async () => {
  clock = now;
  env = { CLAWWEB_MONITORING_ENABLED: "true", CLAWWEB_MONITORING_BOTS_JSON: JSON.stringify(bots) };
  dir = mkdtempSync(join(tmpdir(), "monitoring-contract-"));
  db = database(join(dir, "runtime.sqlite3"));
  await initializeMonitoringSqlite(db);
  await start();
}, 30000);
afterEach(async () => {
  await stop();
  await db?.close();
  if (dir) rmSync(dir, { recursive: true, force: true });
  vi.unstubAllEnvs(); vi.restoreAllMocks();
});

describe("monitoring HTTP -> module schema -> repository -> GET", () => {
  it("persists all decisions and both intervention values, including same Session/different Trace", async () => {
    for (const event of [alert, pass, unresolved]) expect((await post(event)).status).toBe(201);
    const { body, cache } = await get();
    expect(cache).toBe("no-store");
    expect(body).toMatchObject({ total: 2, counts: { all: 2, alert: 1, pass: 1, unresolved: 0 } });
    expect(body.items.map((x: { humanIntervention: boolean }) => x.humanIntervention)).toEqual([false, true]);
    expect(body.items[0]).not.toHaveProperty("notificationStatus");
    for (const field of ["schemaVersion", "eventId", "engine"]) expect(body.items[0]).not.toHaveProperty(field);
    expect((await db.query("SELECT event_id FROM insight_monitoring_diagnoses")).length).toBe(3);
    expect((await get("mock-bot-oc")).body.items[0].occurredAt).toBeNull();
  });
  it("deduplicates 20 concurrent requests and refuses changed content", async () => {
    const results = await Promise.all(Array.from({ length: 20 }, () => post(alert)));
    expect(results.filter((r) => r.status === 201)).toHaveLength(1);
    expect(results.filter((r) => r.status === 200 && r.body.duplicate)).toHaveLength(19);
    expect((await post({ ...alert, businessDiagnosis: "changed" })).status).toBe(409);
    const equivalent = { ...alert, diagnosedAt: "2026-09-09T17:00:18+08:00" }; delete equivalent.handlerName;
    expect((await post(equivalent)).body.duplicate).toBe(true);
    expect((await get()).body.total).toBe(1);
  });
  it("round-trips confidence precisely, 255-char locators, unicode, and case-sensitive IDs", async () => {
    const first = { ...alert, eventId: "Case", diagnosisId: "Case", confidence: 0.12345678901234568, traceId: "中".repeat(255) };
    expect((await post(first)).status).toBe(201);
    expect((await post(first)).body.duplicate).toBe(true);
    expect((await post({ ...first, eventId: "case", diagnosisId: "case" })).status).toBe(201);
    expect((await get()).body.items.map((r: { diagnosisId: string }) => r.diagnosisId)).toEqual(["case", "Case"]);
    expect((await get()).body.items[0].confidence).toBe(first.confidence);
  });
  it("queries 25 rows with DB pagination, decision-independent counts and out-of-range pages", async () => {
    for (let i = 0; i < 25; i++) {
      const base = i % 2 ? pass : alert;
      expect((await post({ ...base, eventId: `diag-${i}`, diagnosisId: `diag-${i}` })).status).toBe(201);
    }
    const result = await get("mock-bot-te", "?page=3&pageSize=10");
    expect(result.body).toMatchObject({ total: 25, totalPages: 3, page: 3 });
    expect(result.body.items).toHaveLength(5);
    const filtered = await get("mock-bot-te", "?decision=ALERT");
    expect(filtered.body).toMatchObject({ total: 13, counts: { all: 25, alert: 13, pass: 12, unresolved: 0 } });
    expect((await get("mock-bot-te", "?page=99")).body).toMatchObject({ total: 25, page: 99, items: [] });
  });
  it("handles Beijing date boundaries and nulls last", async () => {
    for (const [index, time] of [null, "2026-09-08T15:59:59.999Z", "2026-09-08T16:00:00Z", "2026-09-09T15:59:59.999Z", "2026-09-09T16:00:00Z"].entries()) {
      await post({ ...alert, eventId: `boundary-${index}`, diagnosisId: `boundary-${index}`, occurredAt: time });
    }
    expect((await get()).body.items.at(-1).occurredAt).toBeNull();
    expect((await get("mock-bot-te", "?startDate=2026-09-09&endDate=2026-09-09")).body.total).toBe(2);
    expect((await get("mock-bot-te", "?startDate=2026-09-09")).body.total).toBe(3);
    expect((await get("mock-bot-te", "?endDate=2026-09-09")).body.total).toBe(3);
  });
  it("searches literal percent, underscore, backslash and case-insensitive ASCII", async () => {
    await post({ ...alert, sessionKey: "AbC%_\\!中文" });
    await post(pass);
    for (const keyword of ["abc", "%", "_", "\\", "!", "中文"]) {
      expect((await get("mock-bot-te", `?keyword=${encodeURIComponent(keyword)}`)).body.total).toBe(1);
    }
    expect((await get("mock-bot-te", `?keyword=${encodeURIComponent("%' OR 1=1 --")}`)).body.total).toBe(0);
  });
  it("reports fresh/no-conversation checks, expires status and prevents out-of-order overwrite", async () => {
    expect((await post(check, "bot-checks")).body.applied).toBe(true);
    expect((await post(check, "bot-checks")).body.applied).toBe(false);
    expect((await post({ ...check, status: "ERROR" }, "bot-checks")).status).toBe(409);
    expect((await call("/monitoring/bots/mock-bot-te/status")).body).toMatchObject({ status: "HEALTHY", diagnosisCount: 0 });
    const versions = Array.from({ length: 20 }, (_, i) => ({ ...check, checkedAt: new Date(now - i * 1000).toISOString(), lastSuccessfulCheckAt: null, status: "ERROR" }));
    await Promise.all(versions.slice(1).map((c) => post(c, "bot-checks")));
    expect((await call("/monitoring/bots/mock-bot-te/status")).body.status).toBe("HEALTHY");
    clock += 300001;
    expect((await call("/monitoring/bots/mock-bot-te/status")).body).toMatchObject({ status: "UNKNOWN", lastSuccessfulCheckAt: check.lastSuccessfulCheckAt });
  });
  it("atomically selects the newest of concurrent first checks", async () => {
    const responses = await Promise.all(Array.from({ length: 20 }, (_, i) => post({ ...check,
      checkedAt: new Date(now - i * 1000).toISOString(), lastSuccessfulCheckAt: null }, "bot-checks")));
    expect(responses.every((r) => r.status === 200)).toBe(true);
    const rows = await db.query("SELECT * FROM insight_monitoring_bot_checks");
    expect(rows).toHaveLength(1);
    expect(Number(rows[0].checked_at_ms)).toBe(now);
  });
  it("retains history on close and reopen", async () => {
    await post(alert); await stop(); await db.close();
    db = database(join(dir, "runtime.sqlite3")); await start();
    expect((await get()).body.total).toBe(1);
  });
  it("keeps bots with no records and separates their data", async () => {
    await post(alert); await post(check, "bot-checks");
    expect((await call("/monitoring/bots")).body.items).toHaveLength(3);
    expect((await get("empty-bot")).body).toMatchObject({ total: 0, totalPages: 0, items: [] });
    expect((await call("/monitoring/bots/empty-bot/status")).body.status).toBe("UNKNOWN");
    expect((await get("MOCK-BOT-TE")).status).toBe(404);
  });
  it("rejects missing idempotency header, unknown fields and invalid queries", async () => {
    expect((await post(alert, "diagnosis-events", { "Idempotency-Key": "wrong" })).status).toBe(400);
    expect((await post({ ...alert, notificationStatus: "SENT" })).status).toBe(400);
    expect((await post({ ...alert, botId: "unknown" })).status).toBe(403);
    for (const query of ["?page=1&page=2", "?pageSize=100", "?startDate=2026-02-30", "?extra=x"]) {
      expect((await get("mock-bot-te", query)).status).toBe(400);
    }
  });
  it("accepts both reporting endpoints without token configuration or Authorization", async () => {
    expect(env.MONITORING_REPORT_TOKEN).toBeUndefined();
    expect((await post(alert)).status).toBe(201);
    expect((await post(check, "bot-checks")).status).toBe(200);
    expect((await get()).body.total).toBe(1);
  });
  it("ignores legacy reporting credentials without re-enabling token checks", async () => {
    await stop(); env.MONITORING_REPORT_TOKEN = "dummy"; await start();
    expect((await post(alert, "diagnosis-events", { Authorization: "Bearer legacy-other" })).status).toBe(201);
    expect((await post(check, "bot-checks", { Authorization: "Bearer legacy-other" })).status).toBe(200);
  });
  it("fails closed on missing table, missing index and actual write failures", async () => {
    await db.exec("DROP INDEX idx_monitor_diag_bot_time");
    expect((await post(alert)).status).toBe(503);
    expect((await get()).status).toBe(503);
    await db.exec("CREATE INDEX idx_monitor_diag_bot_time ON insight_monitoring_diagnoses (bot_id, occurred_at_ms, event_id)");
    expect((await post(alert)).status).toBe(201);
    const exec = vi.spyOn(db, "exec").mockRejectedValue(new Error("write denied"));
    expect((await post(pass)).status).toBe(503); exec.mockRestore();
    await db.exec("DROP TABLE insight_monitoring_diagnoses");
    expect((await get()).status).toBe(503);
  });
  it("does not ACK a noop database", async () => {
    await stop();
    await start(true, false, () => ({ ...db, dbType: "noop" } as IDatabase));
    expect((await post(alert)).status).toBe(503);
    expect((await get()).status).toBe(503);
  });
  it("rejects malformed JSON at the host boundary and enforces original byte limits", async () => {
    const headers = { "Content-Type": "application/json", "Idempotency-Key": alert.eventId };
    // An upstream Express parser error skips normally mounted child routers. The current
    // host rejects it, but cannot promise our JSON envelope without a host error adapter.
    const bad = await fetch(url + "/internal/monitoring/diagnosis-events", { method: "POST", headers, body: "{" });
    expect(bad.status).toBe(400);
    expect((await get()).body.total).toBe(0);
    const huge = " ".repeat(128 * 1024) + JSON.stringify(alert);
    expect((await call("/internal/monitoring/diagnosis-events", { method: "POST", headers, body: huge })).status).toBe(413);
    await stop(); await start(false);
    const ownBad = await call("/internal/monitoring/diagnosis-events", { method: "POST", headers, body: "{" });
    expect(ownBad.status).toBe(400); expect(ownBad.body.error.code).toBe("MONITORING_INVALID_EVENT");
    expect((await call("/internal/monitoring/diagnosis-events", { method: "POST", headers, body: huge })).status).toBe(413);
  });
  it("rejects unverifiable already-parsed chunked input, but accepts chunking before any parent parser", async () => {
    const chunked = () => new Promise<number>((resolve, reject) => {
      const req = request(url + "/internal/monitoring/diagnosis-events", { method: "POST", headers: {
        "Content-Type": "application/json", "Idempotency-Key": alert.eventId,
        "Transfer-Encoding": "chunked",
      } }, (res) => { res.resume(); res.on("end", () => resolve(res.statusCode!)); });
      req.on("error", reject); req.write(JSON.stringify(alert)); req.end();
    });
    expect(await chunked()).toBe(503);
    await stop(); await start(false);
    expect(await chunked()).toBe(201);
  });
  it("keeps the old factory callable without new OCB parameters, independent of governance readiness", async () => {
    await stop();
    for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
    await start(true, true);
    expect((await call("/monitoring/bots")).body.items).toHaveLength(3);
    // The default factory uses getRepositories(); no initialized shared DB must never fake an ACK.
    expect((await post(alert)).status).toBe(503);
    expect((await call("/overview")).status).toBe(503);
  });
});
