import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { digestRepairJson } from '../contracts/repair-batch.js';
import { RepairReleaseRepository } from './repair-release-repository.js';

type PlannerLease = {
  id: string; workflowId: string; taskId: string; revision: number; stateVersion: number; createdAtMs: number;
  ownerId: string | null; untilMs: number; nextAtMs: number; cursor: string | null;
};
const leaseMs = 300_000;
const isConflict = (error: unknown) => (error as { code?: string })?.code === 'STATE_CONFLICT';

/** One planning pass per environment, using the existing journal PK/CAS (no DDL).
 * This lease reduces duplicate scans; durable per-workflow/job claims still fence dispatch.
 */
export class WorkflowPlannerLeaseRepository {
  constructor(private readonly db: IDatabase) {}
  private async get(id: string): Promise<PlannerLease | null> {
    const rows = await this.db.query<{ body_json: string }>(
      'SELECT body_json FROM workflow_repair_release_records WHERE record_id = ?', [id]);
    return rows[0] ? JSON.parse(rows[0].body_json) : null;
  }
  async claim(environment: string, now: number): Promise<PlannerLease | null> {
    const id = digestRepairJson(['automation-planner/v1', environment]);
    const records = new RepairReleaseRepository(this.db);
    let current = await this.get(id);
    if (!current) {
      // Empty workflow ID cannot collide with a real workflow. This record is
      // scoped by its own task ID and excluded from workflow history queries.
      const initial: PlannerLease = { id, workflowId: '', taskId: id, revision: 0, stateVersion: 0,
        createdAtMs: now, ownerId: null, untilMs: 0, nextAtMs: 0, cursor: null };
      try { await records.insert('auto_planner', { requestId: id }, initial); current = initial; }
      catch (error) { current = await this.get(id); if (!current) throw error; }
    }
    if (current.untilMs > now || current.nextAtMs > now) return null;
    const claimed = { ...current, stateVersion: current.stateVersion + 1, ownerId: randomUUID(), untilMs: now + leaseMs };
    try { await records.update(claimed, current.stateVersion); return claimed; }
    catch (error) { if (isConflict(error)) return null; throw error; }
  }
  async checkpoint(lease: PlannerLease, cursor: string | null, now: number, release = false): Promise<PlannerLease> {
    if (!lease.ownerId || lease.untilMs <= now) throw new Error('AUTOMATION_PLANNER_LEASE_LOST');
    const next = { ...lease, cursor, stateVersion: lease.stateVersion + 1,
      ownerId: release ? null : lease.ownerId, untilMs: release ? 0 : now + leaseMs,
      nextAtMs: release ? now + 15_000 : lease.nextAtMs };
    try { await new RepairReleaseRepository(this.db).update(next, lease.stateVersion); return next; }
    catch (error) { if (isConflict(error)) throw new Error('AUTOMATION_PLANNER_LEASE_LOST'); throw error; }
  }
}
