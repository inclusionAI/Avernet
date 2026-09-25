// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { SessionRecoveryService, DELIVERY_SETTLEMENT_MS } from "../service.js";
import { SessionRecoveryDeliveryRepository } from "../../../repositories/session-recovery-delivery-repository.js";
import { initializeRecoveryDeliverySqlite, renderRecoveryDeliveryDdl } from "../schema.js";
import { mysqlDialect, zdasDialect } from "@avernet/clawweb-shared/server/db/dialect";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { recoveryDatabase } from "./database.js";
import { parseRequest } from "../validation.js";
import { recoveryResult } from "../result.js";
const request = { event_id: "event", tc_fault_label: null, diagnosis: "sanitized diagnostic context", target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" }, session_key: "agent:main:test", delivery_key: "send-1" };
const actor = { userId: "owner", isAdmin: false };
let db: IDatabase, store: SessionRecoveryDeliveryRepository, dir: string, now: number;
const runtime = { sendMessage: vi.fn() }, reporter = { report: vi.fn() }, logger = { log: vi.fn() };
const service = () => new SessionRecoveryService({ runtime, reporter, logger, store, now: () => now });
beforeEach(async () => {
  vi.resetAllMocks(); now = 1000;
  dir = mkdtempSync(join(tmpdir(), "recovery-delivery-")); db = await recoveryDatabase(join(dir, "db.sqlite"));
  store = new SessionRecoveryDeliveryRepository(db);
  runtime.sendMessage.mockResolvedValue({ status: "accepted", platformMessageId: "receipt-1" });
  reporter.report.mockResolvedValue(undefined);
});
afterEach(async () => { vi.restoreAllMocks(); await db?.close(); rmSync(dir, { recursive: true, force: true }); });
it("replays after database reopen without sending or reporting twice; stores no diagnosis", async () => {
  await service().create(request, actor);
  await db.close(); db = await recoveryDatabase(join(dir, "db.sqlite")); store = new SessionRecoveryDeliveryRepository(db);
  now += 10000;
  await service().create(Object.fromEntries(Object.entries(request).reverse()), actor);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1); expect(reporter.report).toHaveBeenCalledTimes(1);
  const rows = await db.query("SELECT * FROM insight_session_recovery_delivery");
  expect(rows).toHaveLength(1); expect(JSON.stringify(rows)).not.toContain(request.diagnosis);
  expect(rows[0]).toMatchObject({ callback_delivered: 1 });
});
it.each([
  { diagnosis: "changed" }, { tc_fault_label: "TC.MCP.PERMISSION" }, { session_key: "agent:main:other" },
  { target: { ...request.target, bot_id: "other" } }, { target: { ...request.target, env: "pre" } },
])("rejects same-key changed content before any second send: %j", async patch => {
  await service().create(request, actor);
  await expect(service().create({ ...request, ...patch }, actor)).rejects.toMatchObject({ status: 409 });
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
});
it("allows a new attempt key and keeps exact event identities distinct", async () => {
  for (const event_id of ["case", "Case", "case ", "01", "1", "中".repeat(128)]) await service().create({ ...request, event_id }, actor);
  await service().create({ ...request, event_id: "case", delivery_key: "send-2" }, actor);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(7);
});
it("claims across independent DB connections while the first send is in flight", async () => {
  let release!: (value: { status: string }) => void;
  runtime.sendMessage.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  const first = service().create(request, actor);
  await vi.waitFor(() => expect(runtime.sendMessage).toHaveBeenCalledTimes(1));
  const otherDb = await recoveryDatabase(join(dir, "db.sqlite"));
  try {
    const other = new SessionRecoveryService({ runtime, reporter, logger, store: new SessionRecoveryDeliveryRepository(otherDb), now: () => now });
    await expect(other.create(request, actor)).rejects.toMatchObject({ status: 503, code: "recovery_in_progress" });
    await expect(other.create({ ...request, diagnosis: "changed" }, actor)).rejects.toMatchObject({ status: 409 });
    release({ status: "accepted" }); await first;
    await other.create(request, actor); expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  } finally { await otherDb.close(); }
});
it("persists the exact callback before reporting and replays it unchanged after a callback outage", async () => {
  reporter.report.mockRejectedValueOnce(Error("receiver unavailable"));
  await expect(service().create(request, actor)).rejects.toThrow("receiver unavailable");
  const original = structuredClone(reporter.report.mock.calls[0][0]);
  await db.close(); db = await recoveryDatabase(join(dir, "db.sqlite")); store = new SessionRecoveryDeliveryRepository(db);
  now += 20000;
  await service().create(request, actor);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(reporter.report.mock.calls.map(call => call[0])).toEqual([original, original]);
  expect((await db.query("SELECT callback_delivered FROM insight_session_recovery_delivery"))[0].callback_delivered).toBe(1);
});
it("retries only the identical callback if the callback succeeded but its acknowledgement write failed", async () => {
  const mark = vi.spyOn(store, "markReported").mockRejectedValueOnce(Error("write unavailable"));
  await expect(service().create(request, actor)).rejects.toThrow("write unavailable");
  mark.mockRestore(); await service().create(request, actor);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(reporter.report.mock.calls[1][0]).toEqual(reporter.report.mock.calls[0][0]);
});
it("settles an abandoned claim as unknown on retry, without ever reclaiming the send", async () => {
  const parsed = parseRequest(request), fingerprint = createHash("sha256").update(JSON.stringify(parsed)).digest("hex");
  await store.claim(parsed, fingerprint, now, now + DELIVERY_SETTLEMENT_MS);
  await db.close(); db = await recoveryDatabase(join(dir, "db.sqlite")); store = new SessionRecoveryDeliveryRepository(db);
  await expect(service().create(request, actor)).rejects.toMatchObject({ code: "recovery_in_progress" });
  now += DELIVERY_SETTLEMENT_MS;
  await service().create(request, actor);
  expect(runtime.sendMessage).not.toHaveBeenCalled();
  expect(reporter.report.mock.calls[0][0].delivery).toMatchObject({ status: "unknown", started_at_ms: 1000, finished_at_ms: now });
  const late = await store.finish(parsed, { result: recoveryResult(parsed, { status: "accepted" }, 1000, now + 1) });
  expect(late.outcome).toMatchObject({ result: { delivery: { status: "unknown" } } });
});
it("fails before sending when the DB write or uniqueness constraint is unavailable", async () => {
  const write = vi.spyOn(db, "exec").mockRejectedValueOnce(Error("disk failure"));
  await expect(service().create(request, actor)).rejects.toThrow("disk failure");
  expect(runtime.sendMessage).not.toHaveBeenCalled(); write.mockRestore();
  await db.exec("DROP TABLE insight_session_recovery_delivery");
  await db.exec("CREATE TABLE insight_session_recovery_delivery (event_id TEXT, delivery_key TEXT)");
  await expect(service().create(request, actor)).rejects.toMatchObject({ status: 503 });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});
it("does not report an unsaved result or resend after result persistence fails", async () => {
  const finish = vi.spyOn(store, "finish").mockRejectedValueOnce(Error("storage unavailable"));
  await expect(service().create(request, actor)).rejects.toThrow("storage unavailable");
  expect(reporter.report).not.toHaveBeenCalled(); finish.mockRestore();
  await expect(service().create(request, actor)).rejects.toMatchObject({ code: "recovery_in_progress" });
  now += DELIVERY_SETTLEMENT_MS; await service().create(request, actor);
  expect(runtime.sendMessage).toHaveBeenCalledTimes(1);
  expect(reporter.report.mock.calls[0][0].delivery.status).toBe("unknown");
});
it("rejects unverified TE before claiming or sending", async () => {
  await expect(service().create({ ...request, target: { ...request.target, engine: "TE" } }, actor)).rejects.toMatchObject({ status: 422 });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
  expect(await db.query("SELECT * FROM insight_session_recovery_delivery")).toEqual([]);
});
it.each([mysqlDialect, zdasDialect])("renders exact full binary keys and millisecond timestamps for $name", dialect => {
  const sql = renderRecoveryDeliveryDdl(dialect);
  expect(sql).toContain("event_id VARBINARY(512)"); expect(sql).toContain("delivery_key VARBINARY(128)");
  expect(sql).toContain("UNIQUE INDEX uk_recovery_delivery (event_id, delivery_key)");
  expect(sql).toContain("started_at_ms BIGINT"); expect(sql).toContain("AUTO_INCREMENT");
});
it("never issues DDL against managed databases", async () => {
  const exec = vi.fn();
  await expect(initializeRecoveryDeliverySqlite({ dbType: "zdas", exec } as unknown as IDatabase)).rejects.toThrow("requires SQLite");
  expect(exec).not.toHaveBeenCalled();
});
