import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { mysqlDialect, zdasDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { initializeMonitoringSqlite, renderMonitoringDdl } from "../schema.js";
import { MonitoringRepository } from "../../../repositories/monitoring-repository.js";
import { parseDiagnosis, parseQuery, parseCheck } from "../validation.js";
import { database } from "./test-database.js";
const fixture = (name: string) =>
  JSON.parse(
    readFileSync(
      join(process.cwd(), "server/fixtures/monitoring", `${name}.json`),
      "utf8",
    ),
  );
const alert = fixture("alert"), pass = fixture("pass"), unresolved = fixture("unresolved");
const now = Date.parse("2026-09-10T09:00:00Z");
let db: IDatabase, repo: MonitoringRepository, dir: string;
beforeEach(async () => {
  dir = mkdtempSync(join(tmpdir(), "monitoring-repository-")); db = database(join(dir, "db.sqlite3"));
  await initializeMonitoringSqlite(db);
  repo = new MonitoringRepository(db);
}, 30000);
afterEach(async () => { await db?.close(); if (dir) rmSync(dir, { recursive: true, force: true }); vi.restoreAllMocks(); });
const insert = (event = alert) => repo.insertDiagnosis(parseDiagnosis(event, event.eventId), now);
const list = (query = {}, bot = "mock-bot-te") => repo.listDiagnoses(bot, parseQuery(query));
describe("monitoring real SQL persistence (no HTTP listener)", () => {
  it("filters before pagination and derives choices independently of selected filters", async () => {
    for (let i = 0; i < 25; i++) await insert({ ...alert, eventId: `filter-${i}`, diagnosisId: `filter-${i}`, businessProblemCategory: "外部服务异常", businessProblemSubtype: i % 2 ? "请求超时" : "数据获取失败" });
    await insert({ ...alert, eventId: "other", diagnosisId: "other", businessProblemCategory: "任务执行异常", businessProblemSubtype: "执行路径缺失" });
    await insert({ ...pass, businessProblemCategory: null, businessProblemSubtype: null });
    const filtered = await list({ businessProblemCategory: "外部服务异常", businessProblemSubtype: "数据获取失败", pageSize: "10", page: "2" });
    expect(filtered).toMatchObject({ total: 13, totalPages: 2, counts: { all: 13, alert: 13, pass: 0 } });
    expect(filtered.items).toHaveLength(3);
    expect(filtered.items.every(row => row.businessProblemSubtype === "数据获取失败")).toBe(true);
    expect(filtered.problemTypes).toHaveLength(2);
    expect(filtered.problemTypes).toContainEqual({ category: "任务执行异常", subtypes: ["执行路径缺失"] });
    const empty = await list({ businessProblemCategory: "' OR 1=1 --" });
    expect(empty.total).toBe(0);
    expect(empty.problemTypes).toHaveLength(2);
    expect((await list({ startDate: "2030-01-01" })).problemTypes).toEqual([]);
    expect((await list({}, "another-bot")).problemTypes).toEqual([]);
  });
  it("initializes only module tables, is repeatable and preserves existing records", async () => {
    await insert();
    await initializeMonitoringSqlite(db);
    expect((await list()).total).toBe(1);
    const tables = await db.query<{ name: string }>("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name");
    expect(tables.map(row => row.name)).toEqual(["insight_monitoring_bot_checks", "insight_monitoring_diagnoses"]);
  });
  it("refuses local initialization on a managed database without executing DDL", async () => {
    const exec = vi.fn();
    await expect(initializeMonitoringSqlite({ dbType: "zdas", exec } as unknown as IDatabase)).rejects.toThrow("requires SQLite");
    expect(exec).not.toHaveBeenCalled();
  });
  it("stores all three decisions and intervention, isolates bots and survives reopen", async () => {
    for (const event of [alert, pass, unresolved]) expect(await insert(event)).toBe(true);
    expect(await list()).toMatchObject({ total: 2, counts: { all: 2, alert: 1, pass: 1, unresolved: 0 } });
    expect((await list()).items.map(x => x.humanIntervention)).toEqual([false, true]);
    expect((await list({}, "mock-bot-oc")).items[0].occurredAt).toBeNull();
    await db.close(); db = database(join(dir, "db.sqlite3")); repo = new MonitoringRepository(db);
    expect((await list()).total).toBe(2);
  });
  it("discovers persisted checks and diagnoses across connections without caching the roster", async () => {
    expect(await repo.listBots()).toEqual([]);
    await insert({ ...alert, botId: "diagnosis-only" });
    const check = parseCheck({ schemaVersion: "claw-monitoring/bot-check/v1", botId: "check-only", engine: "OC",
      checkedAt: new Date(now).toISOString(), lastSuccessfulCheckAt: null, status: "HEALTHY" }, now);
    await repo.applyCheck(check, now);
    const otherDb = database(join(dir, "db.sqlite3"));
    try {
      const other = new MonitoringRepository(otherDb);
      expect(await other.listBots()).toEqual([{ botId: "check-only" }, { botId: "diagnosis-only" }]);
      // The second connection writes both a duplicate identity and new case-sensitive identities.
      await other.applyCheck({ ...check, botId: "diagnosis-only" }, now);
      for (const botId of ["Case", "case"]) await other.applyCheck({ ...check, botId }, now);
      const expected = ["Case", "case", "check-only", "diagnosis-only"].map(botId => ({ botId }));
      expect(await repo.listBots()).toEqual(expected);
      expect(await other.listBots()).toEqual(expected);
    } finally { await otherDb.close(); }
  });
  it("deduplicates concurrent writes, normalizes timezones and rejects conflicts", async () => {
    const results = await Promise.all(Array.from({ length: 20 }, () => insert()));
    expect(results.filter(Boolean)).toHaveLength(1);
    expect(await insert({ ...alert, diagnosedAt: "2026-09-09T17:00:18+08:00" })).toBe(false);
    await expect(insert({ ...alert, businessDiagnosis: "changed" })).rejects.toMatchObject({ code: "EVENT_CONFLICT" });
    expect((await list()).total).toBe(1);
  });
  it("preserves case-sensitive IDs, 255 codepoint locators and confidence", async () => {
    for (const id of ["Case", "case"]) await insert({ ...alert, eventId: id, diagnosisId: id, confidence: 0.12345678901234568, traceId: "中".repeat(255) });
    expect((await list()).items.map(x => x.diagnosisId)).toEqual(["case", "Case"]);
    expect((await list()).items[0].confidence).toBe(0.12345678901234568);
  });
  it("paginates with unfiltered counts and keeps counts on empty pages", async () => {
    for (let i = 0; i < 25; i++) await insert({ ...(i % 2 ? pass : alert), eventId: `d-${i}`, diagnosisId: `d-${i}` });
    expect(await list({ page: "3" })).toMatchObject({ total: 25, totalPages: 3 });
    expect((await list({ page: "3" })).items).toHaveLength(5);
    expect(await list({ decision: "ALERT", page: "99" })).toMatchObject({ total: 13, items: [], counts: { all: 25, pass: 12 } });
  });
  it("uses Beijing bounds and nulls-last ordering", async () => {
    for (const [i, time] of [null, "2026-09-08T15:59:59.999Z", "2026-09-08T16:00:00Z", "2026-09-09T15:59:59.999Z", "2026-09-09T16:00:00Z"].entries()) await insert({ ...alert, eventId: `t-${i}`, diagnosisId: `t-${i}`, occurredAt: time });
    expect((await list()).items.at(-1)?.occurredAt).toBeNull();
    expect((await list({ startDate: "2026-09-09", endDate: "2026-09-09" })).total).toBe(2);
  });
  it("escapes literal search metacharacters", async () => {
    await insert({ ...alert, sessionKey: "AbC%_\\!中文" }); await insert(pass);
    for (const keyword of ["abc", "%", "_", "\\", "!", "中文"]) expect((await list({ keyword })).total).toBe(1);
    expect((await list({ keyword: "%' OR 1=1 --" })).total).toBe(0);
  });
  it("keeps the newest concurrent BotCheck and rejects equal-time conflict", async () => {
    const checks = Array.from({ length: 20 }, (_, i) => parseCheck({ schemaVersion: "claw-monitoring/bot-check/v1", botId: "mock-bot-te", engine: "TE", checkedAt: new Date(now - i * 1000).toISOString(), lastSuccessfulCheckAt: null, status: "HEALTHY" }, now));
    await Promise.all(checks.map(c => repo.applyCheck(c, now)));
    expect((await repo.readStatus("mock-bot-te")).check?.checkedAt).toBe(checks[0].checkedAt);
    expect(await repo.applyCheck(checks[0], now)).toBe(false);
    await expect(repo.applyCheck({ ...checks[0], status: "ERROR" }, now)).rejects.toMatchObject({ code: "EVENT_CONFLICT" });
  });
  it("fails closed on noop/missing indexes/write failure and recovers", async () => {
    await expect(new MonitoringRepository({ ...db, dbType: "noop" } as IDatabase).readStatus("x")).rejects.toMatchObject({ code: "NOT_READY" });
    await db.exec("DROP INDEX idx_monitor_diag_bot_time");
    await expect(insert()).rejects.toMatchObject({ code: "NOT_READY" });
    await db.exec("CREATE INDEX idx_monitor_diag_bot_time ON insight_monitoring_diagnoses(bot_id, occurred_at_ms, event_id)");
    expect(await insert()).toBe(true);
    const exec = vi.spyOn(db, "exec").mockRejectedValue(new Error("write unavailable"));
    await expect(insert(pass)).rejects.toMatchObject({ code: "NOT_READY" }); exec.mockRestore();
    await db.exec("DROP TABLE insight_monitoring_diagnoses");
    await expect(list()).rejects.toMatchObject({ code: "NOT_READY" });
  });
  it.each([mysqlDialect, zdasDialect])("renders reviewed monitoring types without changing legacy VARCHAR policy ($name)", dialect => {
    const sql = renderMonitoringDdl(dialect).join("\n");
    expect(sql).toContain("VARCHAR(255)"); expect(sql).toContain("CHARACTER SET latin1 COLLATE latin1_bin");
    expect(sql).toContain("utf8mb4"); expect(sql).toContain("AUTO_INCREMENT");
    expect(sql).toContain("(bot_id, decision, occurred_at_ms, event_id)");
    expect(sql).not.toContain("unixepoch"); expect(sql).not.toContain("VARCHAR(190)");
    expect(dialect.renderDdl("CREATE TABLE IF NOT EXISTS legacy (name VARCHAR(255))")).toContain("VARCHAR(190)");
  });
});
