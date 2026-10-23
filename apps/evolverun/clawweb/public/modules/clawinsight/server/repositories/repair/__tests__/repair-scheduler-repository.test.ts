import Database from "better-sqlite3";
import { afterEach, beforeEach, expect, it } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { repairSchedulerMigration } from "@avernet/clawweb-shared/server/schema-repair-scheduler";
import { dialectFor } from "@avernet/clawweb-shared/server/db/dialect";
import { RepairSchedulerRepository } from "../repair-scheduler-repository.js";

let db: SqliteDatabase, first: RepairSchedulerRepository, second: RepairSchedulerRepository;
const lease = { environment: "pre" as const, job: "agent_execution" as const, owner: "first" };
const other = { ...lease, owner: "second" };
beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  for (const sql of repairSchedulerMigration.sql) await db.exec(db.dialect.renderDdl(sql));
  first = new RepairSchedulerRepository(db); second = new RepairSchedulerRepository(db);
});
afterEach(async () => { await db.close(); });

it("lets only one competing instance claim a job, with independent environments and jobs", async () => {
  const claims = await Promise.all([first.claim(lease), second.claim(other)]);
  expect(claims.filter(Boolean)).toHaveLength(1);
  expect(await first.claim({ ...lease, environment: "prod" })).toBe(true);
  expect(await second.claim({ ...other, job: "recovery_reporting" })).toBe(true);
});

it("keeps the shared scan interval after release so staggered instances cannot scan again immediately", async () => {
  expect(await first.claim(lease)).toBe(true);
  await first.complete(lease);
  expect(await second.claim(other)).toBe(false);
  await db.exec("UPDATE ce_repair_scheduler_leases SET next_run_at = unixepoch() - 1", []);
  expect(await second.claim(other)).toBe(true);
});

it("allows takeover after expiry and fences stale renewals and completion", async () => {
  await first.claim(lease);
  await db.exec("UPDATE ce_repair_scheduler_leases SET lease_expires_at = unixepoch() - 1, next_run_at = 0", []);
  expect(await first.renew(lease)).toBe(false);
  expect(await second.claim(other)).toBe(true);
  await first.complete(lease);
  expect(await first.isHeld(lease)).toBe(false);
  expect(await second.isHeld(other)).toBe(true);
  expect(await first.renew(lease)).toBe(false);
});

it("renews a live lease and releases only its own identity", async () => {
  await first.claim(lease);
  await db.exec("UPDATE ce_repair_scheduler_leases SET lease_expires_at = unixepoch() + 1", []);
  expect(await first.renew(lease)).toBe(true);
  const [remaining] = await db.query<{ seconds: number }>("SELECT lease_expires_at - unixepoch() AS seconds FROM ce_repair_scheduler_leases");
  expect(remaining.seconds).toBeGreaterThanOrEqual(59);
  await second.complete(other);
  expect(await first.isHeld(lease)).toBe(true);
  await first.complete(lease);
  expect(await first.isHeld(lease)).toBe(false);
});

it.each(["mysql", "zdas"] as const)("renders the additive scheduler schema for %s", engine => {
  const sql = dialectFor(engine).renderDdl(repairSchedulerMigration.sql[0]);
  expect(sql).not.toMatch(/AUTOINCREMENT|unixepoch/);
  expect(sql).toContain("UNIQUE INDEX uk_ce_repair_scheduler_job (environment, job_name)");
});
