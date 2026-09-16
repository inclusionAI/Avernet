import { afterEach, describe, expect, it, vi } from "vitest";
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
afterEach(() => { vi.unstubAllEnvs(); });
function store(): MonitoringStore {
  return { listBots: vi.fn().mockResolvedValue([]), insertDiagnosis: vi.fn().mockResolvedValue(true), applyCheck: vi.fn().mockResolvedValue(true),
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
    const api = createMonitoringService(repo, 300, () => now);
    expect(await api.reportDiagnosis(alert, alert.eventId)).toMatchObject({ duplicate: false, stored: true });
    expect(repo.insertDiagnosis).toHaveBeenCalledWith(parseDiagnosis(alert, alert.eventId), now);
    expect(await api.reportCheck(check)).toMatchObject({ applied: true });
    expect(repo.applyCheck).toHaveBeenCalledWith(parseCheck(check, now), now);
  });
  it("accepts new bots but still rejects invalid engines and IDs before storage", async () => {
    const repo = store();
    const api = createMonitoringService(repo, 300, () => now);
    await expect(api.reportDiagnosis({ ...alert, botId: "new-bot" }, alert.eventId)).resolves.toMatchObject({ accepted: true });
    vi.mocked(repo.insertDiagnosis).mockClear();
    await expect(api.reportDiagnosis({ ...alert, engine: "OTHER" }, alert.eventId)).rejects.toMatchObject({ code: "INVALID_EVENT" });
    await expect(api.reportCheck({ ...check, botId: "bad/id" })).rejects.toMatchObject({ code: "INVALID_EVENT" });
    await expect(api.status("unknown")).rejects.toMatchObject({ code: "BOT_NOT_FOUND" });
    await expect(api.diagnoses("unknown", {})).rejects.toMatchObject({ code: "BOT_NOT_FOUND" });
    expect(repo.insertDiagnosis).not.toHaveBeenCalled();
    expect(repo.applyCheck).not.toHaveBeenCalled();
  });
  it("expires old checks, keeps last successful time and honors reported pause", async () => {
    const repo = store();
    vi.mocked(repo.readStatus).mockResolvedValue({ check: parseCheck(check, now), count: 3 });
    let clock = now;
    const api = createMonitoringService(repo, 300, () => clock);
    expect((await api.status(alert.botId)).status).toBe("HEALTHY");
    clock += 300001;
    expect(await api.status(alert.botId)).toMatchObject({ status: "UNKNOWN", diagnosisCount: 3, lastSuccessfulCheckAt: check.lastSuccessfulCheckAt });
    vi.mocked(repo.readStatus).mockResolvedValue({ check: parseCheck({ ...check, status: "PAUSED" }, now), count: 3 });
    expect((await api.status(alert.botId)).status).toBe("PAUSED");
    vi.mocked(repo.readStatus).mockResolvedValue({ check: null, count: 3 });
    expect(await api.status(alert.botId)).toMatchObject({ status: "UNKNOWN", diagnosisCount: 3, lastSuccessfulCheckAt: null });
  });
  it("reads discovered bots from storage on every call", async () => {
    const repo = store(); const api = createMonitoringService(repo, 300);
    expect(await api.bots()).toEqual({ items: [] });
    vi.mocked(repo.listBots).mockResolvedValue([{ botId: "new-bot" }]);
    expect(await api.bots()).toEqual({ items: [{ botId: "new-bot" }] });
    expect(repo.listBots).toHaveBeenCalledTimes(2);
  });
  it("never turns a failed storage operation into success", async () => {
    const repo = store();
    vi.mocked(repo.insertDiagnosis).mockRejectedValue(new Error("offline"));
    const api = createMonitoringService(repo, 300);
    await expect(api.reportDiagnosis(alert, alert.eventId)).rejects.toThrow("offline");
  });
  it("assembles without a DB; unavailable storage fails requests, not Host startup", async () => {
    const getDb = vi.fn(() => { throw new Error("not initialized"); });
    const runtime = createMonitoringRuntime(getDb);
    expect(runtime.service).not.toBeNull();
    expect(getDb).not.toHaveBeenCalled();
    await expect(runtime.service!.bots()).rejects.toMatchObject({ code: "NOT_READY" });
    await expect(runtime.service!.status("mock-bot-te")).rejects.toMatchObject({ code: "NOT_READY" });
  });
  it.each([{}, { CLAWWEB_MONITORING_ENABLED: "false" },
    { CLAWWEB_MONITORING_ENABLED: "invalid", CLAWWEB_MONITORING_BOTS_JSON: "not-json" },
    { CLAWWEB_MONITORING_BOTS_JSON: "[]" },
    { CLAWWEB_MONITORING_BOTS_JSON: '[{"botId":"default","engine":"TE"},{"botId":"default","engine":"OC"}]' },
  ])("ignores removed configuration %j", (legacy) => {
    const getDb = vi.fn(() => { throw new Error("not initialized"); });
    for (const [key, value] of Object.entries(legacy)) vi.stubEnv(key, value);
    expect(createMonitoringRuntime(getDb).service).not.toBeNull();
    expect(getDb).not.toHaveBeenCalled();
  });
  it.each([undefined, "", "0", "-1", "invalid", "1.5", " 300 ", "1", "600", "9007199254740992"])("ignores removed stale configuration: %s", (seconds) => {
    vi.stubEnv("CLAWWEB_MONITORING_STALE_SECONDS", seconds);
    const getDb = vi.fn(() => { throw new Error("not initialized"); });
    expect(createMonitoringRuntime(getDb).service).not.toBeNull();
    expect(getDb).not.toHaveBeenCalled();
  });
});
