// @vitest-environment node
import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { distributedLockMigrations } from "@avernet/clawweb-shared/server/schema-distributed-lock";
import { DistributedLockRepository } from "@avernet/clawweb-shared/server/repositories/distributed-lock-repository";
import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import { createSessionRecoveryRuntime } from "../index.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { logRepairEvent } from "../../common/logging.js";
vi.mock("../../common/logging.js", async importOriginal => ({
  ...await importOriginal<typeof import("../../common/logging.js")>(), logRepairEvent: vi.fn(),
}));

const databases: SqliteDatabase[] = [];
beforeEach(() => { vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval", "performance"] }); vi.setSystemTime(0); vi.clearAllMocks(); });
afterEach(async () => {
  vi.useRealTimers(); vi.restoreAllMocks();
  for (const db of databases.splice(0)) await db.close();
});
const scope = { environment: getCurrentEnv() === "prod" ? "prod" : "pre",
  scene: "session_recovery_maintenance", tenantId: "__global__", resourceKey: "scan", leaseMs: 120_000 };

async function fixture(provision = true) {
  const raw = new Database(":memory:");
  raw.function("recovery_test_now_ms", () => Date.now());
  const db = new SqliteDatabase(raw); databases.push(db);
  Object.assign(db, { dialect: { ...db.dialect, nowMsSql: "recovery_test_now_ms()" } });
  if (provision) await distributedLockMigrations[0].migrate!(db);
  const maintenance = vi.spyOn(SessionRecoveryTaskService.prototype, "maintenance").mockResolvedValue(undefined);
  const create = () => createSessionRecoveryRuntime({ db, repo: new EvolveRepository(db),
    runtime: { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() },
    targets: { resolve: vi.fn() }, ais: {} as AisExecutor, nasRoots: [], snapshotId: 1,
    publicBaseUrl: "https://fixture.invalid", artifactStore: { getObject: vi.fn(), putObject: vi.fn() } })!;
  return { db, create, runtime: create(), maintenance, leases: new DistributedLockRepository(db) };
}

it("never reads ce_tasks or enters maintenance when another replica owns the shared lock", async () => {
  const f = await fixture();
  await f.leases.claim(scope);
  const query = vi.spyOn(f.db, "query");
  await f.runtime.maintenance!();
  expect(f.maintenance).not.toHaveBeenCalled();
  expect(query.mock.calls.some(([sql]) => /\bce_tasks\b|\bworkflow_repair_release_records\b/.test(sql))).toBe(false);
  expect(logRepairEvent).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it("does not attempt maintenance or request-path DDL when the independent lock table is absent", async () => {
  const f = await fixture(false), query = vi.spyOn(f.db, "query"), exec = vi.spyOn(f.db, "exec");
  await expect(f.runtime.maintenance!()).rejects.toThrow("Incompatible clawweb_distributed_locks");
  expect(f.maintenance).not.toHaveBeenCalled();
  expect(query.mock.calls.some(([sql]) => /\bce_tasks\b/.test(sql))).toBe(false);
  expect(exec).not.toHaveBeenCalled();
});

it("propagates lock write errors without falling back to an unlocked scan", async () => {
  const f = await fixture(), failure = new Error("lock database unavailable"), exec = f.db.exec.bind(f.db);
  vi.spyOn(f.db, "exec").mockImplementation((sql, params) => sql.startsWith("UPDATE clawweb_distributed_locks")
    ? Promise.reject(failure) : exec(sql, params));
  await expect(f.runtime.maintenance!()).rejects.toBe(failure);
  expect(f.maintenance).not.toHaveBeenCalled();
});

it("uses the existing fixed logger fields to correlate one successful held scan", async () => {
  const f = await fixture();
  await f.runtime.maintenance!();
  expect(f.maintenance).toHaveBeenCalledOnce();
  expect(logRepairEvent).toHaveBeenCalledWith("session-recovery", "session_recovery_maintenance", "scan_started",
    { environment: scope.environment, stateVersion: 1 });
  expect(logRepairEvent).toHaveBeenCalledWith("session-recovery", "session_recovery_maintenance", "scan_completed",
    { environment: scope.environment, stateVersion: 1, outcome: "succeeded", elapsedMs: 0 });
  expect(await f.db.query("SELECT owner_token FROM clawweb_distributed_locks")).toEqual([{ owner_token: null }]);
  expect(vi.getTimerCount()).toBe(0);
});

it("releases its lock while preserving an original maintenance failure", async () => {
  const f = await fixture(), failure = new Error("scan database unavailable");
  f.maintenance.mockRejectedValue(failure);
  await expect(f.runtime.maintenance!()).rejects.toBe(failure);
  expect(await f.db.query("SELECT owner_token FROM clawweb_distributed_locks")).toEqual([{ owner_token: null }]);
  expect(vi.getTimerCount()).toBe(0);
});

it("keeps the global 60-second start cadence after quick release and staggered replica wakeups", async () => {
  const f = await fixture(), other = f.create();
  await Promise.all([f.runtime.maintenance!(), other.maintenance!()]);
  expect(f.maintenance).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(8_000);
  const query = vi.spyOn(f.db, "query");
  await other.maintenance!();
  expect(f.maintenance).toHaveBeenCalledOnce();
  expect(query.mock.calls.some(([sql]) => /\bce_tasks\b/.test(sql))).toBe(false);
  await vi.advanceTimersByTimeAsync(52_000);
  await other.maintenance!();
  expect(f.maintenance).toHaveBeenCalledTimes(2);
  expect(await f.db.query("SELECT id FROM clawweb_distributed_locks")).toHaveLength(1);
});
