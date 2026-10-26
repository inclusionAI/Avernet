import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { RecoveryTaskState } from "../../repair/session-recovery/task-contracts.js";
import { recoveryTaskResult } from "../../repair/session-recovery/task-state.js";
import { parseRecoveryResult } from "../recovery-result-validation.js";
import { MonitoringRepository } from "../../../repositories/monitoring-repository.js";
import { appendRecoveryResult } from "../../../repositories/monitoring-recovery-results.js";
import { initializeMonitoringSqlite } from "../schema.js";
import { parseDiagnosis } from "../validation.js";
import { resolved } from "./identity-fixtures.js";
import { database } from "./test-database.js";

const event = JSON.parse(readFileSync(join(process.cwd(), "server/fixtures/monitoring/alert.json"), "utf8"));
const result = { event_id: event.eventId, task_id: "SR-test", result_version: 3,
  status: "waiting_effect" as const, phase: "observe" as const, waiting_reason: "effect" as const,
  effect: "unknown" as const, result_text: "等待效果", evidence_ids: [] };
let db: IDatabase, repo: MonitoringRepository, dir: string;
beforeEach(async () => {
  dir = mkdtempSync(join(tmpdir(), "monitoring-recovery-")); db = database(join(dir, "db.sqlite3"));
  await initializeMonitoringSqlite(db); repo = new MonitoringRepository(db);
  await repo.insertDiagnosis(resolved(parseDiagnosis(event, event.eventId)), 123);
});
afterEach(async () => { vi.restoreAllMocks(); await db?.close(); if (dir) rmSync(dir, { recursive: true, force: true }); });
const read = async () => (await db.query("SELECT * FROM insight_monitoring_diagnose WHERE event_id = ?", [event.eventId]))[0];
const history = async () => JSON.parse(String((await read()).self_healing_results));

describe("recovery callback durable append contract", () => {
  it("appends JSON objects without changing original diagnosis or its idempotency", async () => {
    const before = await read();
    expect(await repo.appendRecoveryResult(result)).toBe(true);
    expect(await history()).toEqual([result]);
    const after = await read();
    for (const key of Object.keys(before).filter(k => !["self_healing_results", "gmt_modified"].includes(k))) expect(after[key]).toEqual(before[key]);
    expect(await repo.insertDiagnosis(resolved(parseDiagnosis(event, event.eventId)), 999)).toBe(false);
    expect(await history()).toEqual([result]);
  });
  it("deduplicates reordered object keys; conflicts never overwrite", async () => {
    await repo.appendRecoveryResult(result);
    expect(await repo.appendRecoveryResult(Object.fromEntries(Object.entries(result).reverse()) as typeof result)).toBe(false);
    await expect(repo.appendRecoveryResult({ ...result, result_text: "不同结果" })).rejects.toMatchObject({ code: "RESULT_CONFLICT" });
    expect(await history()).toHaveLength(1);
  });
  it("preserves out-of-order versions and multiple tasks across concurrent connections", async () => {
    const otherDb = database(join(dir, "db.sqlite3"));
    try {
      const other = new MonitoringRepository(otherDb);
      await Promise.all([repo.appendRecoveryResult(result), other.appendRecoveryResult({ ...result, result_version: 5 })]);
      await other.appendRecoveryResult({ ...result, result_version: 1 });
      await repo.appendRecoveryResult({ ...result, task_id: "SR-another" });
      const rows = await history();
      expect(rows).toHaveLength(4);
      expect(rows.map((r: typeof result) => `${r.task_id}:${r.result_version}`).sort()).toEqual(["SR-another:3", "SR-test:1", "SR-test:3", "SR-test:5"]);
    } finally { await otherDb.close(); }
  });
  it("does not fabricate a diagnosis when event_id is missing", async () => {
    await expect(repo.appendRecoveryResult({ ...result, event_id: "missing" })).rejects.toMatchObject({ code: "RESULT_TARGET_NOT_FOUND" });
    expect((await read()).self_healing_results).toBeNull();
  });
  it.each(['not-json', '{}', '["json-string"]', '[{}]'])("preserves corrupt stored history: %s", async text => {
    await db.exec("UPDATE insight_monitoring_diagnose SET self_healing_results = ?", [text]);
    await expect(repo.appendRecoveryResult(result)).rejects.toMatchObject({ code: "NOT_READY" });
    expect((await read()).self_healing_results).toBe(text);
  });
  it("propagates write failures and bounded CAS exhaustion, never false success", async () => {
    const exec = vi.spyOn(db, "exec").mockRejectedValueOnce(new Error("write failed"));
    await expect(repo.appendRecoveryResult(result)).rejects.toMatchObject({ code: "NOT_READY" });
    exec.mockResolvedValue({ affectedRows: 0 });
    await expect(repo.appendRecoveryResult(result)).rejects.toMatchObject({ code: "NOT_READY" });
    expect(exec).toHaveBeenCalledTimes(9);
    expect((await read()).self_healing_results).toBeNull();
  });
  it("uses binary comparison for non-null MySQL history and parameterized event ids", async () => {
    const old = JSON.stringify([{ ...result, result_version: 1 }]);
    const query = vi.fn().mockResolvedValue([{ self_healing_results: old }]);
    const exec = vi.fn().mockResolvedValue({ affectedRows: 1 });
    await appendRecoveryResult({ dbType: "mysql", query, exec } as unknown as IDatabase, result);
    expect(exec.mock.calls[0][0]).toContain("CAST(self_healing_results AS BINARY) = CAST(? AS BINARY)");
    expect(exec.mock.calls[0][1]).toEqual([JSON.stringify([{ ...result, result_version: 1 }, result]), result.event_id, old]);
  });
});

describe("existing sender/receiver compatibility", () => {
  it.each(["none", "performed", "unknown"] as const)("preserves intervention_status=%s alongside legacy history", async intervention_status => {
    await repo.appendRecoveryResult(result);
    const input = { ...result, result_version: 4, intervention_status };
    expect(parseRecoveryResult(input)).toEqual(input);
    expect(await repo.appendRecoveryResult(input)).toBe(true);
    expect(await history()).toEqual([result, input]);
    expect(await repo.appendRecoveryResult(result)).toBe(false);
    expect(await repo.appendRecoveryResult(input)).toBe(false);
    await expect(repo.appendRecoveryResult({ ...input, intervention_status: intervention_status === "none" ? "unknown" : "none" })).rejects.toMatchObject({ code: "RESULT_CONFLICT" });
  });
  it.each(["waiting_effect", "completed", "failed", "canceled"] as const)("accepts real generated %s with optional delivery/evidence", async status => {
    const evidence = { evidence_id: "e1", tool_call_id: "call1", source: "session", location: "current", excerpt: "执行结果" };
    const state: RecoveryTaskState = {
      schemaVersion: "session-recovery-task/v1", taskId: "SR-generated", version: 5, status, phase: "verify", level: "approval",
      request: { event_id: event.eventId, tc_fault_label: null, target: { bot_id: "bot", entity_id: "owner", env: "prod", engine: "OC" },
        session_key: "agent:main:session", delivery_key: "key", diagnosis: "未完成" },
      observationDeadlineAt: null, ownerDeadlineAt: null, plan: null,
      judgment: { delivery: "confirmed", response: "acted", effect: status === "completed" ? "recovered" : "unknown", summary: "结果", evidence_ids: ["e1"] },
      evidence: [evidence], delivery: { event_id: event.eventId, delivery: { delivery_key: "key", status: "accepted", result_text: "已投递",
        result_payload: { message_id: "message1" }, started_at_ms: 100, finished_at_ms: 200, error: null } },
    };
    const generated = recoveryTaskResult(state);
    expect(parseRecoveryResult(generated)).toEqual(generated);
    await repo.appendRecoveryResult(generated);
    expect(await history()).toEqual([generated]);
  });
  it("does not invent an evidence-count limit or silently remove optional fields", () => {
    const input = { ...result, error: null, evidence: Array.from({ length: 70 }, (_, i) => ({ evidence_id: `${i}`, tool_call_id: "c", source: "s", location: "l", excerpt: "e" })) };
    expect(parseRecoveryResult(input)).toEqual(input);
  });
  it.each([{ result_version: 0 }, { result_version: 1.1 }, { result_version: true }, { status: ["completed"] },
    { status: "running" }, { waiting_reason: null }, { evidence_ids: [1] }, { evidence: [{}] }, { delivery: {} }, { error: 1 },
    { intervention_status: true }, { intervention_status: null }, { intervention_status: "accepted" }, { intervention_status: ["none"] }, { unrecognized: "none" }])("rejects invalid patch %j", patch => {
    expect(() => parseRecoveryResult({ ...result, ...patch })).toThrowError(expect.objectContaining({ code: "INVALID_RESULT" }));
  });
});


it.each(["none", "performed", "unknown"] as const)("stores normal user handoff with actual intervention_status=%s and keeps legacy history", async intervention_status => {
  await repo.appendRecoveryResult(result);
  const input = { ...result, result_version: 4, status: "completed" as const, waiting_reason: null,
    completion_reason: "user_continued" as const, intervention_status, result_text: "用户已接手，本次自愈已让路" };
  expect(parseRecoveryResult(input)).toEqual(input);
  expect(await repo.appendRecoveryResult(input)).toBe(true);
  expect(await history()).toEqual([result, input]);
  expect(await repo.appendRecoveryResult(input)).toBe(false);
});

it("stores confirmed target unavailability as normal completion without claiming recovery", async () => {
  const input = { ...result, result_version: 7, status: "completed" as const, waiting_reason: null,
    completion_reason: "target_unavailable" as const, effect: "unknown" as const, intervention_status: "none" as const,
    result_text: "目标沙箱已销毁，本次自愈结束" };
  expect(parseRecoveryResult(input)).toEqual(input);
  expect(await repo.appendRecoveryResult(input)).toBe(true);
  expect(await history()).toEqual([input]);
});

it.each([{ completion_reason: "other", status: "completed", waiting_reason: null },
  { completion_reason: null, status: "completed", waiting_reason: null },
  { completion_reason: "user_continued" }])("rejects an invalid normal completion reason: %j", patch => {
  expect(() => parseRecoveryResult({ ...result, ...patch })).toThrowError(expect.objectContaining({ code: "INVALID_RESULT" }));
});


it.each(["owner_handoff", "mcp_permission_required"] as const)("persists %s independently from business recovery and intervention", async reason => {
  const input = { ...result, result_version: 8, status: "completed" as const, waiting_reason: null,
    completion_reason: reason, effect: "not_recovered" as const, intervention_status: "none" as const,
    result_text: "MCP 权限需 Owner 自行处理，本次自愈已结束" };
  expect(parseRecoveryResult(input)).toEqual(input);
  expect(await repo.appendRecoveryResult(input)).toBe(true);
  expect(await history()).toEqual([input]);
});
