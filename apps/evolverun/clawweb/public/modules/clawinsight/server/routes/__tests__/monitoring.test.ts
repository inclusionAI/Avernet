import { initializeMonitoringSqlite } from "../../services/monitoring/schema.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { request } from "node:http";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../repositories/repair/session-recovery-task-repository.js";
import { SessionRecoveryTaskService } from "../../services/repair/session-recovery/task-service.js";
import { createMonitoringRecoveryTrigger, SESSION_RECOVERY_ALLOWLIST_KEY } from "../../services/monitoring/session-recovery-trigger.js";
import { DIAGNOSIS_V3_VERSION } from "../../services/monitoring/contracts.js";
import { runtimeIdentity } from "../../services/monitoring/__tests__/runtime-identity-fixture.js";
import { MonitoringError } from "../../services/monitoring/contracts.js";
import type { MonitoringRecoveryTrigger } from "../../services/monitoring/contracts.js";
import { database } from "../../services/monitoring/__tests__/test-database.js";
import { fixtureIntegration } from "../../services/monitoring/__tests__/identity-fixtures.js";
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
const check = { schemaVersion: "claw-monitoring/bot-check/v2", entityId: "001234", env: "test", botId: "mock-bot-te", engine: "TE",
  checkedAt: new Date(now).toISOString(), lastSuccessfulCheckAt: new Date(now).toISOString(), status: "HEALTHY" };
let db: IDatabase, dir: string, url: string;
let server: ReturnType<express.Application["listen"]> | undefined;
let clock: number;
let principal = { staffId: '001234', isAuthenticated: true, isClawInsightAdmin: true, tenant: 'test', allowedTargetEnvs: ['test'] };
let env: Record<string, string | undefined>;
let clawInsightAdmin: boolean | undefined = true;
let recovery: MonitoringRecoveryTrigger | undefined;
async function start(parent = true, defaultFactory = false, getDb = () => db, unassembled = false) {
  const app = express();
  app.use((req, _res, next) => { req.isClawInsightAdmin = clawInsightAdmin; next(); });
  if (parent) app.use(express.json({ limit: "10mb" }));
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  const runtime = unassembled ? { service: null } : createMonitoringRuntime(getDb, () => clock, { ...fixtureIntegration,
    ...(recovery ? { recovery, scope: { ...fixtureIntegration.scope, allowedTargetEnvs: ["test", "prod"] } } : {}), principal: async () => principal });
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
  principal = { staffId: '001234', isAuthenticated: true, isClawInsightAdmin: true, tenant: 'test', allowedTargetEnvs: ['test'] };
  clawInsightAdmin = true;
  recovery = undefined;
  env = {};
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
  it("accepts type filters and returns database-derived choices over HTTP", async () => {
    await post(alert); await post(pass);
    const params = new URLSearchParams({ businessProblemCategory: alert.businessProblemCategory, businessProblemSubtype: alert.businessProblemSubtype });
    const result = await get("mock-bot-te", `?${params}`);
    expect(result.status).toBe(200);
    expect(result.body.total).toBe(1);
    expect(result.body.problemTypes).toContainEqual({ category: alert.businessProblemCategory, subtypes: [alert.businessProblemSubtype] });
    expect((await get("mock-bot-te", "?businessProblemSubtype=orphan")).status).toBe(400);
  });
  it.each([{}, { CLAWWEB_MONITORING_ENABLED: "false", CLAWWEB_MONITORING_BOTS_JSON: "invalid-json" },
    { CLAWWEB_MONITORING_ENABLED: "true", CLAWWEB_MONITORING_BOTS_JSON: '[{"botId":"old-only","engine":"TE"}]' },
  ])("discovers 26 bots from real writes without a restart or configuration: %j", async (legacy) => {
    await stop(); env = legacy; await start();
    expect((await call("/monitoring/bots")).body).toEqual({ items: [] });
    const expected: { botId: string; entityId: string; env: string }[] = [];
    for (let i = 0; i < 26; i++) {
      const botId = `bot-${String(i).padStart(2, "0")}`, engine = i < 17 ? "OC" : "TE";
      expected.push({ botId, entityId: "001234", env: "test" });
      expect((await post({ ...check, botId, engine }, "bot-checks")).status).toBe(200);
      const eventId = `event-${i}`;
      expect((await post({ ...alert, botId, engine, eventId, diagnosisId: eventId })).status).toBe(201);
    }
    expect((await call("/monitoring/bots")).body).toEqual({ items: expected });
    expect((await get("bot-25")).body.total).toBe(1);
    await stop(); await db.close(); db = database(join(dir, "runtime.sqlite3")); await start();
    expect((await call("/monitoring/bots")).body).toEqual({ items: expected });
  });
  it.each([undefined, "", "0", "-1", "invalid", "1.5", " 300 ", "1", "600", "9007199254740992"])(
    "keeps GET/POST available and preserves stored status despite stale env %s", async (seconds) => {
      await stop(); env.CLAWWEB_MONITORING_STALE_SECONDS = seconds; await start();
      expect(await call("/monitoring/bots")).toMatchObject({ status: 200, body: { items: [] } });
      expect((await post(check, "bot-checks")).status).toBe(200);
      expect((await post(alert)).status).toBe(201);
      clock = now + 300000;
      expect((await call("/monitoring/bots/mock-bot-te/status")).body.status).toBe("HEALTHY");
      clock++;
      expect(await call("/monitoring/bots/mock-bot-te/status")).toMatchObject({ status: 200,
        body: { status: "HEALTHY", lastSuccessfulCheckAt: check.lastSuccessfulCheckAt } });
      // An explicitly reported ERROR stays visible even without another check.
      expect((await post({ ...check, checkedAt: new Date(clock).toISOString(), status: "ERROR" }, "bot-checks")).status).toBe(200);
      clock += 7 * 86400000;
      expect((await call("/monitoring/bots/mock-bot-te/status")).body.status).toBe("ERROR");
      clock++;
      expect((await post({ ...check, checkedAt: new Date(clock).toISOString(), status: "PAUSED" }, "bot-checks")).status).toBe(200);
      clock += 300001;
      expect((await call("/monitoring/bots/mock-bot-te/status")).body.status).toBe("PAUSED");
      expect((await call("/monitoring/bots")).body.items).toEqual([{ botId: check.botId, entityId: check.entityId, env: check.env }]);
    },
  );
  it("discovers historical diagnosis-only bots and keeps case-sensitive identities", async () => {
    await post({ ...alert, botId: "Case" });
    expect((await post({ ...check, botId: "case" }, "bot-checks")).status).toBe(200);
    expect((await call("/monitoring/bots")).body.items).toEqual(["Case", "case"].map(botId => ({ botId, entityId: "001234", env: "test" })));
    expect((await call("/monitoring/bots/Case/status")).body).toMatchObject({ status: "UNKNOWN", diagnosisCount: 1 });
    expect((await get("never-reported")).status).toBe(404);
  });
  it("does not discover rejected or failed writes and propagates list storage errors", async () => {
    expect((await post({ ...alert, engine: "OTHER" })).status).toBe(400);
    const exec = vi.spyOn(db, "exec").mockRejectedValue(new Error("write denied"));
    expect((await post(alert)).status).toBe(503);
    expect((await post(check, "bot-checks")).status).toBe(503);
    exec.mockRestore();
    expect((await call("/monitoring/bots")).body).toEqual({ items: [] });
    const query = vi.spyOn(db, "query").mockRejectedValue(new Error("read denied"));
    expect((await call("/monitoring/bots")).status).toBe(503);
    query.mockRestore();
    expect((await call("/monitoring/bots")).body).toEqual({ items: [] });
  });
  it.each([false, undefined])("protects all browser reads for flag %s but allows both internal reports", async (flag) => {
    clawInsightAdmin = flag;
    principal.isClawInsightAdmin = flag === true;
    expect((await post(alert)).status).toBe(201);
    expect((await post(check, "bot-checks")).status).toBe(200);
    for (const path of ["/monitoring/bots", "/monitoring/bots/mock-bot-te/status", "/monitoring/bots/mock-bot-te/diagnoses"]) {
      const denied = await call(path);
      expect(denied.status).toBe(403);
      expect(denied.cache).toBe("no-store");
    }
    // Governance keeps its existing readiness behavior, not the monitoring role guard.
    expect((await call("/overview")).status).toBe(503);
    clawInsightAdmin = true;
    principal.isClawInsightAdmin = true;
    for (const path of ["/monitoring/bots", "/monitoring/bots/mock-bot-te/status", "/monitoring/bots/mock-bot-te/diagnoses"]) {
      expect((await call(path)).status).toBe(200);
    }
  });
  it("persists all decisions and both intervention values, including same Session/different Trace", async () => {
    for (const event of [alert, pass, unresolved]) expect((await post(event)).status).toBe(201);
    const { body, cache } = await get();
    expect(cache).toBe("no-store");
    expect(body).toMatchObject({ total: 2, counts: { all: 2, alert: 1, pass: 1, unresolved: 0 } });
    expect(body.items.map((x: { humanIntervention: boolean }) => x.humanIntervention)).toEqual([false, true]);
    expect(body.items[0]).not.toHaveProperty("notificationStatus");
    for (const field of ["schemaVersion", "eventId", "engine"]) expect(body.items[0]).not.toHaveProperty(field);
    expect((await db.query("SELECT event_id FROM insight_monitoring_diagnose")).length).toBe(3);
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
  it("reports fresh/no-conversation checks, preserves status and prevents out-of-order overwrite", async () => {
    expect((await post(check, "bot-checks")).body.applied).toBe(true);
    expect((await post(check, "bot-checks")).body.applied).toBe(false);
    expect((await post({ ...check, status: "ERROR" }, "bot-checks")).status).toBe(409);
    expect((await call("/monitoring/bots/mock-bot-te/status")).body).toMatchObject({ status: "HEALTHY", diagnosisCount: 0 });
    const versions = Array.from({ length: 20 }, (_, i) => ({ ...check, checkedAt: new Date(now - i * 1000).toISOString(), lastSuccessfulCheckAt: null, status: "ERROR" }));
    await Promise.all(versions.slice(1).map((c) => post(c, "bot-checks")));
    expect((await call("/monitoring/bots/mock-bot-te/status")).body.status).toBe("HEALTHY");
    clock += 300001;
    expect((await call("/monitoring/bots/mock-bot-te/status")).body).toMatchObject({ status: "HEALTHY", lastSuccessfulCheckAt: check.lastSuccessfulCheckAt });
  });
  it("atomically selects the newest of concurrent first checks", async () => {
    const responses = await Promise.all(Array.from({ length: 20 }, (_, i) => post({ ...check,
      checkedAt: new Date(now - i * 1000).toISOString(), lastSuccessfulCheckAt: null }, "bot-checks")));
    expect(responses.every((r) => r.status === 200)).toBe(true);
    const rows = await db.query("SELECT * FROM insight_monitoring_bot_check");
    expect(rows).toHaveLength(1);
    expect(Number(rows[0].checked_at_ms)).toBe(now);
  });
  it("retains history on close and reopen", async () => {
    await post(alert); await stop(); await db.close();
    db = database(join(dir, "runtime.sqlite3")); await start();
    expect((await get()).body.total).toBe(1);
  });
  it("discovers checks-only bots and separates their data", async () => {
    await post(alert); await post(check, "bot-checks");
    await post({ ...check, botId: "empty-bot", status: "UNKNOWN", lastSuccessfulCheckAt: null }, "bot-checks");
    expect((await call("/monitoring/bots")).body.items).toHaveLength(2);
    expect((await get("empty-bot")).body).toMatchObject({ total: 0, totalPages: 0, items: [] });
    expect((await call("/monitoring/bots/empty-bot/status")).body.status).toBe("UNKNOWN");
    expect((await get("MOCK-BOT-TE")).status).toBe(404);
  });
  it("rejects missing idempotency header, unknown fields and invalid queries", async () => {
    expect((await post(alert, "diagnosis-events", { "Idempotency-Key": "wrong" })).status).toBe(400);
    expect((await post({ ...alert, notificationStatus: "SENT" })).status).toBe(400);
    expect((await post({ ...alert, botId: "bad/id" })).status).toBe(400);
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
    await db.exec("DROP INDEX idx_monitor_diag_target_time");
    expect((await post(alert)).status).toBe(503);
    expect((await get()).status).toBe(503);
    await db.exec("CREATE INDEX idx_monitor_diag_target_time ON insight_monitoring_diagnose (bot_id, entity_id, env, occurred_at_ms, event_id)");
    expect((await post(alert)).status).toBe(201);
    const exec = vi.spyOn(db, "exec").mockRejectedValue(new Error("write denied"));
    expect((await post(pass)).status).toBe(503); exec.mockRestore();
    await db.exec("DROP TABLE insight_monitoring_diagnose");
    expect((await get()).status).toBe(503);
  });
  it("still rejects explicitly unassembled monitoring without blaming removed configuration", async () => {
    await stop(); await start(true, false, () => db, true);
    expect(await call("/monitoring/bots")).toMatchObject({ status: 503,
      body: { error: { code: "MONITORING_NOT_READY", message: "监控模块未装配。" } } });
    expect((await post(check, "bot-checks")).status).toBe(503);
    expect((await post(alert)).status).toBe(503);
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
    expect((await call("/monitoring/bots")).body).toEqual({ items: [] });
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
    expect((await call("/monitoring/bots")).status).toBe(503);
    // The default factory uses getRepositories(); no initialized shared DB must never fake an ACK.
    expect((await post(alert)).status).toBe(503);
    expect((await call("/overview")).status).toBe(503);
  });
});


describe('monitoring browser HTTP identity and enrollment boundary', () => {
  beforeEach(() => { principal.isClawInsightAdmin = false; });
  it('does not grant legacy reads from a forged host role flag', async () => {
    clawInsightAdmin = true;
    expect((await call('/monitoring/bots')).status).toBe(403);
  });
  it('allows members without the legacy admin flag, rejects scope escalation and ignores forged headers', async () => {
    clawInsightAdmin = false;
    expect((await call('/monitoring/bot-options')).status).toBe(200);
    expect((await call('/monitoring/bot-options?scope=all')).status).toBe(403);
    expect((await call('/monitoring/bots')).status).toBe(403);
    principal.isAuthenticated = false;
    const denied = await call('/monitoring/bot-options', { headers: { 'X-User-Id': '001234', Authorization: 'Bearer fake' } });
    expect(denied).toMatchObject({ status: 401, body: { error: { code: 'MONITORING_UNAUTHENTICATED' } }, cache: 'no-store' });
  });
  it('returns the exact 501 placeholder without writes and reauthorizes every detail request', async () => {
    const { createMonitoringReferences } = await import('../../services/monitoring/target-ref.js');
    const ref = createMonitoringReferences().encode({ botId: 'mock-bot-te', entityId: '001234', env: 'test' }, 'test');
    const path = `/monitoring/targets/${ref}`;
    const enroll = () => call('/monitoring/enrollments', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ botRef: ref }) });
    expect((await call(`${path}/status`)).body).toMatchObject({ enrollmentState: 'NOT_ENROLLED', monitoring: null });
    expect((await call(`${path}/diagnoses`)).status).toBe(409);
    const exec = vi.spyOn(db, 'exec');
    expect(await enroll()).toMatchObject({ status: 501, body: { error: { code: 'MONITORING_ENROLLMENT_NOT_IMPLEMENTED' } } });
    expect(exec).not.toHaveBeenCalled();
    await post(check, 'bot-checks');
    expect((await enroll()).status).toBe(409);
    expect((await call(`${path}/status`)).body.enrollmentState).toBe('ENROLLED');
    principal.staffId = 'other';
    for (const suffix of ['/status', '/diagnoses']) expect((await call(path + suffix)).status).toBe(404);
    expect((await enroll()).status).toBe(404);
  });
});

describe("diagnosis report -> allowlist -> durable recovery task", () => {
  async function automaticRecovery(botId = "mock-bot-te") {
    await stop();
    await runMigrations(db, "sqlite");
    const config = new AppConfigRepository(db);
    await config.create({ config_key: SESSION_RECOVERY_ALLOWLIST_KEY, config_json: JSON.stringify({
      allowed_bots: [{ bot_id: botId, entity_id: "001234", env: "prod" }],
    }) });
    const objects = new Map<string, Buffer>();
    const artifacts = {
      putObject: async (key: string, content: Buffer) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
      getObject: async (key: string) => ({ content: objects.get(key)!, etag: null, contentType: "application/json" }),
    };
    const tasks = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), artifacts);
    const agent = { start: vi.fn().mockResolvedValue(undefined), release: vi.fn().mockResolvedValue(undefined), listExecutions: vi.fn().mockResolvedValue([]),
      terminate: vi.fn().mockResolvedValue({ status: "remote_stopped", aisJobId: "job" }) };
    const service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks, agent,
      notifier: { notify: vi.fn() }, reporter: { report: vi.fn() }, observationMs: 3600000 });
    recovery = createMonitoringRecoveryTrigger({ config, service });
    await start();
    return { config, tasks, service, agent, artifacts,
      input: { ...alert, botId, env: "prod", handlerName: "NOT_COMPLETED", sessionKey: "agent:main:session:original:user:001234" } };
  }

  it("keeps the diagnosis on task storage failure, then creates exactly one task on duplicate retry", async () => {
    const { input, artifacts, tasks, agent } = await automaticRecovery();
    const upload = vi.spyOn(artifacts, "putObject").mockRejectedValueOnce(new Error("object store unavailable"));
    expect(await post(input)).toMatchObject({ status: 503, body: { error: { code: "MONITORING_NOT_READY" } } });
    expect(await db.query("SELECT event_id FROM insight_monitoring_diagnose")).toHaveLength(1);
    expect(await db.query("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'")).toHaveLength(0);
    expect(await post(input)).toMatchObject({ status: 200, body: { stored: true, duplicate: true } });
    expect(await post(input)).toMatchObject({ status: 200, body: { duplicate: true } });
    const rows = await db.query<{ task_id: string }>("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'");
    expect(rows).toHaveLength(1);
    expect(await tasks.find(rows[0].task_id)).toMatchObject({ level: "approval", phase: "assess",
      request: { event_id: input.eventId, target: { bot_id: input.botId, entity_id: input.entityId, env: "prod", engine: "TE" } } });
    await vi.waitFor(() => expect(agent.start).toHaveBeenCalledTimes(1));
    expect(upload).toHaveBeenCalled();
    expect((await post({ ...input, businessDiagnosis: "changed" })).status).toBe(409);
    expect(await db.query("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'")).toHaveLength(1);
  });

  it("deduplicates concurrent reports and retries after an acknowledgment is lost", async () => {
    const { input, agent } = await automaticRecovery();
    const responses = await Promise.all(Array.from({ length: 10 }, () => post(input)));
    expect(responses.every(response => response.status === 200 || response.status === 201)).toBe(true);
    expect(responses.filter(response => response.status === 201)).toHaveLength(1);
    expect((await post(input)).body.duplicate).toBe(true);
    expect(await db.query("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'")).toHaveLength(1);
    await vi.waitFor(() => expect(agent.start).toHaveBeenCalledTimes(1));
  });

  it("creates one durable OC v3 recovery task with normalized instance locators", async () => {
    const { input, tasks, agent } = await automaticRecovery("mock-bot-oc");
    const event = { ...input, schemaVersion: DIAGNOSIS_V3_VERSION, engine: "OC",
      runtimeIdentity: { ...runtimeIdentity, BOT_ID: input.botId, OWNER_ID: input.entityId } };
    expect((await post(event)).status).toBe(201);
    expect((await post(event)).body.duplicate).toBe(true);
    const rows = await db.query<{ task_id: string }>("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'");
    expect(rows).toHaveLength(1);
    const task = await tasks.find(rows[0].task_id);
    expect(task?.request.target).toEqual({ bot_id: input.botId, entity_id: input.entityId, env: "prod", engine: "OC",
      runtime_identity: { stage: "online", deviceUuid: runtimeIdentity.CLIENT_ID, version: "V16" } });
    const [stored] = await db.query("SELECT runtime_identity FROM insight_monitoring_diagnose WHERE event_id = ?", [event.eventId]);
    expect(JSON.parse(String(stored.runtime_identity))).toEqual(event.runtimeIdentity);
    expect(task?.request.target.runtime_identity).not.toHaveProperty("TOKEN");
    await vi.waitFor(() => expect(agent.start).toHaveBeenCalledTimes(1));
  });

  it("acknowledges nonmembers normally and retries enabled configuration failures without losing diagnostics", async () => {
    const { input, config, agent } = await automaticRecovery();
    expect((await post({ ...input, entityId: "another-owner" })).status).toBe(201);
    expect(agent.start).not.toHaveBeenCalled();
    await config.update(SESSION_RECOVERY_ALLOWLIST_KEY, { config_json: "null" });
    expect((await post(input)).status).toBe(409);
    const next = { ...input, eventId: "another-event", diagnosisId: "another-event" };
    expect((await post(next)).status).toBe(503);
    await config.update(SESSION_RECOVERY_ALLOWLIST_KEY, { enabled: 0 });
    expect(await post(next)).toMatchObject({ status: 200, body: { duplicate: true } });
    expect(await db.query("SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery'")).toHaveLength(0);
    expect(agent.start).not.toHaveBeenCalled();
  });
});


describe("recovery result receiver HTTP contract", () => {
  const result = { event_id: alert.eventId, task_id: "SR-http", result_version: 3,
    status: "completed", phase: "verify", waiting_reason: null, effect: "recovered", result_text: "已恢复", evidence_ids: [] };
  const report = (body: Record<string, unknown>) => post(body, "session-recovery-results");
  it("persists and acknowledges identical replay without requiring an extra token", async () => {
    await post(alert);
    expect(await report(result)).toMatchObject({ status: 200, body: { accepted: true, eventId: alert.eventId, taskId: "SR-http", resultVersion: 3, duplicate: false } });
    expect(await report(result)).toMatchObject({ status: 200, body: { duplicate: true } });
    expect(await report({ ...result, result_text: "conflict" })).toMatchObject({ status: 409, body: { error: { code: "MONITORING_RESULT_CONFLICT" } } });
    expect(await post(alert)).toMatchObject({ status: 200, body: { duplicate: true } });
  });
  it("returns actionable errors without fabricating success", async () => {
    expect(await report(result)).toMatchObject({ status: 404, body: { error: { code: "MONITORING_RESULT_TARGET_NOT_FOUND" } } });
    expect(await report({ ...result, result_version: 0 })).toMatchObject({ status: 422, body: { error: { code: "MONITORING_INVALID_RESULT" } } });
    await post(alert);
    await db.exec("UPDATE insight_monitoring_diagnose SET self_healing_results = ?", ["broken"]);
    expect(await report(result)).toMatchObject({ status: 503, body: { error: { code: "MONITORING_NOT_READY" } } });
  });
  it.each([true, false])("enforces exact 128 KiB bytes with host parser=%s", async parent => {
    await stop(); await start(parent); await post(alert);
    const base = { ...result, result_text: "" };
    const padding = 128 * 1024 - Buffer.byteLength(JSON.stringify(base));
    expect((await report({ ...base, result_text: "x".repeat(padding) })).status).toBe(200);
    expect((await report({ ...base, result_text: "x".repeat(padding + 1) })).status).toBe(413);
  });
});


it("stores the complete v3 snapshot without leaking it in ACKs, UI DTOs or rejection responses", async () => {
  await stop();
  recovery = { trigger: vi.fn().mockRejectedValue(new MonitoringError("INVALID_EVENT", "Recovery rejected; diagnostic saved")) };
  await start();
  const event = { ...unresolved, decision: "ALERT", handlerName: "NOT_COMPLETED", schemaVersion: DIAGNOSIS_V3_VERSION,
    runtimeIdentity: { ...runtimeIdentity, BOT_ID: unresolved.botId } };
  const rejected = await post(event);
  expect(rejected.status).toBe(400);
  const [stored] = await db.query("SELECT runtime_identity, entity_id FROM insight_monitoring_diagnose WHERE event_id = ?", [event.eventId]);
  expect(JSON.parse(String(stored.runtime_identity))).toEqual(event.runtimeIdentity);
  expect(stored.entity_id).toBe(event.entityId);
  expect(recovery.trigger).toHaveBeenCalledWith(expect.objectContaining({ runtimeIdentity: event.runtimeIdentity, entityId: event.entityId }));
  await stop(); recovery = undefined; await start();
  const ack = await post(event);
  expect(ack.status).toBe(200);
  expect(ack.body.duplicate).toBe(true);
  const page = await get(event.botId);
  expect(page.body.items[0]).not.toHaveProperty("runtimeIdentity");
  for (const response of [rejected, ack, page]) {
    expect(JSON.stringify(response)).not.toContain(runtimeIdentity.TOKEN);
    expect(JSON.stringify(response)).not.toContain(runtimeIdentity.CLIENT_ID);
  }
});
