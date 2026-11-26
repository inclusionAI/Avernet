// @vitest-environment node
import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { RecoveryMaintenanceLease } from "../contracts.js";
import { createSessionRecoveryRuntime } from "../index.js";
import { SessionRecoveryTaskService } from "../task-service.js";

const databases: SqliteDatabase[] = [];
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0); });
afterEach(async () => {
  vi.useRealTimers(); vi.restoreAllMocks();
  for (const db of databases.splice(0)) await db.close();
});
function fixture() {
  const db = new SqliteDatabase(new Database(":memory:")); databases.push(db);
  const owner = { expiresAtMs: 120_000, renew: vi.fn(async (now: number) => now + 120_000),
    release: vi.fn(async (_now: number) => {}) };
  const provider: RecoveryMaintenanceLease = { claim: vi.fn().mockResolvedValue(owner) };
  const maintenance = vi.spyOn(SessionRecoveryTaskService.prototype, "maintenance").mockResolvedValue(undefined);
  const runtime = createSessionRecoveryRuntime({ db, repo: new EvolveRepository(db), maintenanceLease: provider,
    runtime: { sendMessage: vi.fn(), request: vi.fn(), executeShell: vi.fn() },
    targets: { resolve: vi.fn() }, ais: {} as AisExecutor, nasRoots: [], snapshotId: 1,
    publicBaseUrl: "https://fixture.invalid", artifactStore: { getObject: vi.fn(), putObject: vi.fn() } })!;
  return { runtime, owner, provider, maintenance };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
it("skips the complete scan when another replica owns the lease", async () => {
  const f = fixture(); vi.mocked(f.provider.claim).mockResolvedValue(null);
  await f.runtime.maintenance!();
  expect(f.maintenance).not.toHaveBeenCalled();
  expect(f.owner.release).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
it("propagates lease-store errors without falling back to an unlocked scan", async () => {
  const f = fixture(); vi.mocked(f.provider.claim).mockRejectedValue(new Error("lease database unavailable"));
  await expect(f.runtime.maintenance!()).rejects.toThrow("lease database unavailable");
  expect(f.maintenance).not.toHaveBeenCalled();
});
it("releases its lease even when the original maintenance fails", async () => {
  const f = fixture(); f.maintenance.mockRejectedValue(new Error("scan database unavailable"));
  await expect(f.runtime.maintenance!()).rejects.toThrow("scan database unavailable");
  expect(f.owner.release).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});
it("renews a slow scan every 20 seconds and keeps it valid beyond the initial expiry", async () => {
  const f = fixture(), scan = deferred<void>();
  f.maintenance.mockImplementation(async assertLease => { await scan.promise; assertLease?.(); });
  const running = f.runtime.maintenance!();
  await vi.advanceTimersByTimeAsync(140_000);
  expect(f.owner.renew).toHaveBeenCalledTimes(7);
  scan.resolve(); await running;
  expect(f.owner.release).toHaveBeenCalledWith(140_000);
  expect(vi.getTimerCount()).toBe(0);
});
it("makes subsequent maintenance checkpoints fail after a renewal error", async () => {
  const f = fixture(), scan = deferred<void>();
  f.owner.renew.mockRejectedValue(new Error("lease displaced"));
  f.maintenance.mockImplementation(async assertLease => { await scan.promise; assertLease?.(); });
  const running = expect(f.runtime.maintenance!()).rejects.toThrow("lease displaced");
  await vi.advanceTimersByTimeAsync(20_000);
  scan.resolve(); await running;
  expect(f.owner.release).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
it("waits for an in-flight renewal before releasing the current lease version", async () => {
  const f = fixture(), scan = deferred<void>(), renewal = deferred<number>();
  f.owner.renew.mockImplementation(() => renewal.promise);
  f.maintenance.mockImplementation(async () => { await scan.promise; });
  const running = f.runtime.maintenance!();
  await vi.advanceTimersByTimeAsync(20_000);
  scan.resolve(); await vi.advanceTimersByTimeAsync(0);
  expect(f.owner.release).not.toHaveBeenCalled();
  renewal.resolve(140_000); await running;
  expect(f.owner.release).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it("does not revive an expired scan when its delayed renewal eventually completes", async () => {
  const f = fixture(), scan = deferred<void>(), renewal = deferred<number>();
  f.owner.renew.mockImplementation(() => renewal.promise);
  f.maintenance.mockImplementation(async assertLease => { await scan.promise; assertLease?.(); });
  const running = expect(f.runtime.maintenance!()).rejects.toThrow("SESSION_RECOVERY_MAINTENANCE_LEASE_LOST");
  await vi.advanceTimersByTimeAsync(120_001);
  scan.resolve(); await vi.advanceTimersByTimeAsync(0);
  renewal.resolve(240_001); await running;
  expect(f.owner.release).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
