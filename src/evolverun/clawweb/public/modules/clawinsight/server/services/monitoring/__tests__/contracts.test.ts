import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createMonitoringService } from "../monitoring-service.js";
import { parseCheck, parseDiagnosis, parseQuery, timestamp } from "../validation.js";
import { CHECK_VERSION, type MonitoringStore } from "../contracts.js";
import { createMonitoringRuntime } from "../monitoring-runtime.js";

const fixture = (name: string) =>
  JSON.parse(
    readFileSync(
      join(process.cwd(), "server/fixtures/monitoring", `${name}.json`),
      "utf8",
    ),
  );
const alert = fixture("alert");
const now = Date.parse("2026-09-10T09:00:00Z");
const check = { schemaVersion: CHECK_VERSION, botId: "mock-bot-te", engine: "TE", checkedAt: new Date(now).toISOString(),
  lastSuccessfulCheckAt: new Date(now).toISOString(), status: "HEALTHY" };
const env = { CLAWWEB_MONITORING_ENABLED: "true", CLAWWEB_MONITORING_BOTS_JSON: '[{"botId":"mock-bot-te","engine":"TE"}]' };
function store(): MonitoringStore {
  return { insertDiagnosis: vi.fn().mockResolvedValue(true), applyCheck: vi.fn().mockResolvedValue(true),
    readStatus: vi.fn().mockResolvedValue({ check: null, count: 0 }), listDiagnoses: vi.fn() };
}
describe("monitoring contract validation", () => {
  it("normalizes optional fields, equivalent timezones and keeps real intervention", () => {
    const a = parseDiagnosis(alert, alert.eventId);
    const v = { ...alert, diagnosedAt: "2026-09-09T17:00:18+08:00" };
    delete v.handlerName;
    expect(parseDiagnosis(v, alert.eventId)).toEqual(a);
    expect(a.humanIntervention).toBe(true);
  });
  it.each([
    { eventId: "bad/id" }, { botId: "中文" }, { diagnosisId: "different" }, { traceId: null },
    { sessionId: " " }, { confidence: true }, { confidence: 1.1 }, { confidence: NaN },
    { humanIntervention: 1 }, { humanIntervention: undefined }, { notificationStatus: "SENT" },
    { decision: "PASS" }, { engine: "OTHER" }, { systemDiagnosis: "\ud800" },
  ])("rejects invalid diagnosis patch %j", (patch) => {
    expect(() => parseDiagnosis({ ...alert, ...patch }, alert.eventId)).toThrow();
  });
  it("rejects missing idempotency header, arrays and overlong text without truncation", () => {
    expect(() => parseDiagnosis(alert, undefined)).toThrow();
    expect(() => parseDiagnosis([alert], alert.eventId)).toThrow();
    expect(() => parseDiagnosis({ ...alert, systemDiagnosis: "中".repeat(8001) }, alert.eventId)).toThrow();
    expect(parseDiagnosis({ ...alert, traceId: "中".repeat(255) }, alert.eventId).traceId).toHaveLength(255);
  });
  it.each(["2026-02-29T00:00:00Z", "2026-09-10", "2026-09-10T00:00:00", "2026-09-10T24:00:00Z",
    "2026-09-10T00:00:60Z", "2026-09-10T00:00:00.1234Z", "2026-09-10T00:00:00+24:00"])("rejects invalid timestamp %s", (v) => {
    expect(() => timestamp(v)).toThrow();
  });
  it("accepts leap days and milliseconds", () => {
    expect(timestamp("2024-02-29T08:00:00.1+08:00")).toBe("2024-02-29T00:00:00.100Z");
  });
  it("validates bot check time and required nullable key", () => {
    expect(parseCheck(check, now).status).toBe("HEALTHY");
    expect(() => parseCheck({ ...check, checkedAt: new Date(now + 300001).toISOString() }, now)).toThrow();
    expect(() => parseCheck({ ...check, lastSuccessfulCheckAt: new Date(now + 1).toISOString() }, now)).toThrow();
    const { lastSuccessfulCheckAt: _last, ...missing } = check;
    expect(() => parseCheck(missing, now)).toThrow();
  });
  it("converts inclusive Beijing days to half-open UTC bounds", () => {
    expect(parseQuery({ startDate: "2026-09-09", endDate: "2026-09-09" })).toMatchObject({
      startMs: Date.parse("2026-09-08T16:00:00Z"), endMs: Date.parse("2026-09-09T16:00:00Z"), page: 1, pageSize: 10,
    });
  });
  it.each([{ page: ["1", "2"] }, { page: "0" }, { page: "1.1" }, { page: "2147483648" }, { pageSize: "25" },
    { startDate: "2026-02-30" }, { startDate: "2026-09-10", endDate: "2026-09-09" }, { ownerUserId: "someone" }])("rejects query %j", (query) => {
    expect(() => parseQuery(query)).toThrow();
  });
});
describe("monitoring service and configuration", () => {
  it("invokes storage with normalized event and stable receive time", async () => {
    const repo = store();
    const api = createMonitoringService(repo, [{ botId: alert.botId, engine: "TE" }], 300, () => now);
    expect(await api.reportDiagnosis(alert, alert.eventId)).toMatchObject({ duplicate: false, stored: true });
    expect(repo.insertDiagnosis).toHaveBeenCalledWith(parseDiagnosis(alert, alert.eventId), now);
    expect(await api.reportCheck(check)).toMatchObject({ applied: true });
    expect(repo.applyCheck).toHaveBeenCalledWith(parseCheck(check, now), now);
  });
  it("rejects unknown bots and engine mismatches before storage", async () => {
    const repo = store();
    const api = createMonitoringService(repo, [{ botId: alert.botId, engine: "OC" }], 300);
    await expect(api.reportDiagnosis(alert, alert.eventId)).rejects.toMatchObject({ code: "BOT_NOT_ALLOWED" });
    await expect(api.status("unknown")).rejects.toMatchObject({ code: "BOT_NOT_FOUND" });
    expect(repo.insertDiagnosis).not.toHaveBeenCalled();
  });
  it("expires old checks, keeps last successful time and honors explicit pause", async () => {
    const repo = store();
    vi.mocked(repo.readStatus).mockResolvedValue({ check: parseCheck(check, now), count: 3 });
    let clock = now;
    const api = createMonitoringService(repo, [{ botId: alert.botId, engine: "TE" }, { botId: "paused", engine: "TE", paused: true }], 300, () => clock);
    expect((await api.status(alert.botId)).status).toBe("HEALTHY");
    clock += 300001;
    expect(await api.status(alert.botId)).toMatchObject({ status: "UNKNOWN", diagnosisCount: 3, lastSuccessfulCheckAt: check.lastSuccessfulCheckAt });
    expect((await api.status("paused")).status).toBe("PAUSED");
  });
  it("never turns a failed storage operation into success", async () => {
    const repo = store();
    vi.mocked(repo.insertDiagnosis).mockRejectedValue(new Error("offline"));
    const api = createMonitoringService(repo, [{ botId: alert.botId, engine: "TE" }], 300);
    await expect(api.reportDiagnosis(alert, alert.eventId)).rejects.toThrow("offline");
  });
  it("lists bots without a DB; unavailable DB fails only dependent calls", async () => {
    const getDb = vi.fn(() => { throw new Error("not initialized"); });
    const runtime = createMonitoringRuntime(getDb, env);
    expect(runtime.service!.bots()).toEqual({ items: [{ botId: "mock-bot-te" }] });
    expect(getDb).not.toHaveBeenCalled();
    await expect(runtime.service!.status("mock-bot-te")).rejects.toMatchObject({ code: "NOT_READY" });
  });
  it.each([{ CLAWWEB_MONITORING_ENABLED: "false" }, { CLAWWEB_MONITORING_STALE_SECONDS: "0" },
    { CLAWWEB_MONITORING_BOTS_JSON: "[]" }, { CLAWWEB_MONITORING_BOTS_JSON: '[{"botId":"a","engine":"TE","name":"ignored?"}]' },
    { CLAWWEB_MONITORING_BOTS_JSON: '[{"botId":"a","engine":"TE"},{"botId":"a","engine":"OC"}]' }])("fails closed for configuration %j", (patch) => {
    expect(createMonitoringRuntime(() => { throw new Error(); }, { ...env, ...patch }).service).toBeNull();
  });
});
