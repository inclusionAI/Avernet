// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { repairFixture } from '../../services/__tests__/repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { WorkflowPlannerLeaseRepository } from '../workflow-planner-lease-repository.js';

const fixtures: Awaited<ReturnType<typeof repairFixture>>[] = [];
afterEach(async () => { for (const f of fixtures.splice(0)) await f.db.close(); });
async function fixture() {
  const f = await repairFixture(0); fixtures.push(f);
  for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
  return { ...f, a: new WorkflowPlannerLeaseRepository(f.db), b: new WorkflowPlannerLeaseRepository(f.db) };
}
it('allows only one contender to initialize and claim an environment', async () => {
  const f = await fixture();
  const claims = await Promise.all([f.a.claim('prod', 100), f.b.claim('prod', 100)]);
  expect(claims.filter(Boolean)).toHaveLength(1);
  expect(await f.b.claim('pre', 100)).not.toBeNull();
  expect(await f.db.query("SELECT record_id FROM workflow_repair_release_records WHERE record_kind='auto_planner'")).toHaveLength(2);
});
it('retains the cursor across release and enforces a shared cooldown before another replica runs', async () => {
  const f = await fixture();
  const owner = (await f.a.claim('prod', 100))!;
  await f.a.checkpoint(owner, 'workflow-10', 200, true);
  expect(await f.b.claim('prod', 201)).toBeNull();
  expect(await f.b.claim('prod', 15_200)).toBeNull();
  expect(await f.b.claim('prod', 60_199)).toBeNull();
  expect(await f.b.claim('prod', 60_200)).toMatchObject({ cursor: 'workflow-10' });
});
it('recovers a crashed planner after expiry and fences delayed writes from its old owner', async () => {
  const f = await fixture();
  const old = (await f.a.claim('prod', 100))!;
  expect(await f.b.claim('prod', 300_099)).toBeNull();
  const replacement = (await f.b.claim('prod', 300_100))!;
  expect(replacement.ownerId).not.toBe(old.ownerId);
  // Even with an old sampled timestamp, CAS must reject the displaced owner.
  await expect(f.a.checkpoint(old, 'stale', 200, true)).rejects.toThrow('AUTOMATION_PLANNER_LEASE_LOST');
  await f.b.checkpoint(replacement, 'current', 300_101, true);
  expect(await f.a.claim('prod', 360_100)).toBeNull();
  expect(await f.a.claim('prod', 360_101)).toMatchObject({ cursor: 'current' });
});
it('does not renew its own expired lease without reacquiring it', async () => {
  const f = await fixture();
  const owner = (await f.a.claim('prod', 100))!;
  await expect(f.a.checkpoint(owner, null, 300_100)).rejects.toThrow('AUTOMATION_PLANNER_LEASE_LOST');
});
