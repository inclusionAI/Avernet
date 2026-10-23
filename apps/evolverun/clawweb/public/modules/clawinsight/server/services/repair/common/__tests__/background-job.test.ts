import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { repairSchedulerMigration } from "@avernet/clawweb-shared/server/schema-repair-scheduler";
import { RepairSchedulerRepository } from "../../../../repositories/repair/repair-scheduler-repository.js";
import { startRepairBackgroundJob } from "../background-job.js";

let db: SqliteDatabase, stops: Array<() => Promise<void>>;
beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  for (const sql of repairSchedulerMigration.sql) await db.exec(db.dialect.renderDdl(sql));
  stops = []; vi.useFakeTimers();
});
afterEach(async () => { await Promise.all(stops.map(stop => stop())); vi.useRealTimers(); vi.restoreAllMocks(); await db.close(); });

it("runs a shared scan once across timers and keeps HTTP's event-loop turn available during an async wait", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const run = vi.fn(async () => { await gate; });
  const options = { db, controlPlaneEnvironment: "pre" as const, job: "agent_execution" as const, run };
  stops.push(startRepairBackgroundJob(options), startRepairBackgroundJob(options));
  try {
    await vi.advanceTimersByTimeAsync(20000);
    expect(run).toHaveBeenCalledOnce();
    let responsive = false;
    setImmediate(() => { responsive = true; });
    await vi.advanceTimersByTimeAsync(1);
    expect(responsive).toBe(true);
    await vi.advanceTimersByTimeAsync(80000);
    expect(run).toHaveBeenCalledOnce();
  } finally { release(); await vi.advanceTimersByTimeAsync(1); }
});

it("stops starting work on lease loss and cannot release a successor's lease", async () => {
  let release!: () => void, active!: () => Promise<boolean>;
  const gate = new Promise<void>(resolve => { release = resolve; });
  stops.push(startRepairBackgroundJob({ db, controlPlaneEnvironment: "pre", job: "agent_execution",
    run: async isActive => { active = isActive; await gate; expect(await isActive()).toBe(false); } }));
  try {
    await vi.advanceTimersByTimeAsync(20000);
    await db.exec("UPDATE ce_repair_scheduler_leases SET lease_expires_at = unixepoch() - 1, next_run_at = 0", []);
    const successor = { environment: "pre" as const, job: "agent_execution" as const, owner: "successor" };
    const leases = new RepairSchedulerRepository(db);
    expect(await leases.claim(successor)).toBe(true);
    expect(await active()).toBe(false);
    release(); await vi.advanceTimersByTimeAsync(1);
    expect(await leases.isHeld(successor)).toBe(true);
  } finally { release(); }
});

it("does not fall back to uncoordinated scanning when the lease table is unavailable", async () => {
  await db.exec("DROP TABLE ce_repair_scheduler_leases", []);
  const run = vi.fn(), log = vi.spyOn(console, "error").mockImplementation(() => {});
  stops.push(startRepairBackgroundJob({ db, controlPlaneEnvironment: "pre", job: "agent_execution", run }));
  try {
    await vi.advanceTimersByTimeAsync(40000);
    expect(run).not.toHaveBeenCalled();
    expect(log).toHaveBeenCalled();
  } finally { log.mockRestore(); }
});

it("stops admitting work when lease renewal fails", async () => {
  let active!: () => Promise<boolean>, release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(RepairSchedulerRepository.prototype, "renew").mockRejectedValue(new Error("database unavailable"));
  stops.push(startRepairBackgroundJob({ db, controlPlaneEnvironment: "pre", job: "agent_execution",
    run: async isActive => { active = isActive; await gate; } }));
  try {
    await vi.advanceTimersByTimeAsync(20000);
    expect(await active()).toBe(true);
    await vi.advanceTimersByTimeAsync(20000);
    expect(await active()).toBe(false);
  } finally { release(); await vi.advanceTimersByTimeAsync(1); }
});
