import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { VersionObservation } from '../contracts/workflow-version-comparison.js';

const terminal = "'succeeded', 'failed', 'aborted', 'cancelled', 'canceled'";
// Runtime adapters historically sent either epoch seconds or milliseconds.
const started = '(CASE WHEN started_at >= 1000000000000 THEN started_at ELSE started_at * 1000 END)';
export class WorkflowVersionComparisonRepository {
  constructor(private db: IDatabase) {}
  async versions(workflowId: string) {
    return this.db.query<{ deploy_number: number; version: number; is_active: number; gmt_create: number | string }>(
      `SELECT deploy_number, version, is_active, gmt_create FROM workflow_deploy_history
       WHERE workflow_id = ? AND action IN ('deploy', 'rollback', 'pull', 'migration') ORDER BY deploy_number DESC`, [workflowId]);
  }
  async observe(workflowId: string, deployNumber: number, fromMs: number, toMs: number): Promise<VersionObservation> {
    const [row] = await this.db.query<{ terminal: number; succeeded: number; failed: number; unfinished: number }>(
      `SELECT SUM(CASE WHEN status IN (${terminal}) THEN 1 ELSE 0 END) AS terminal,
       SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
       SUM(CASE WHEN status IN ('failed', 'aborted', 'cancelled', 'canceled') THEN 1 ELSE 0 END) AS failed,
       SUM(CASE WHEN status NOT IN (${terminal}) THEN 1 ELSE 0 END) AS unfinished
       FROM flow_runs WHERE workflow_id = ? AND workflow_deploy_number = ? AND ${started} >= ? AND ${started} < ?`,
      [workflowId, deployNumber, fromMs, toMs]);
    const terminalRuns = Number(row?.terminal ?? 0), succeededRuns = Number(row?.succeeded ?? 0);
    return { deployNumber, fromMs, toMs, terminalRuns, succeededRuns, failedRuns: Number(row?.failed ?? 0),
      successRate: terminalRuns ? succeededRuns / terminalRuns * 100 : null, excludedUnfinished: Number(row?.unfinished ?? 0) };
  }
  async unknown(workflowId: string, windows: Array<{ fromMs: number; toMs: number }>): Promise<number> {
    if (!windows.length) return 0;
    const [row] = await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM flow_runs fr
      WHERE fr.workflow_id = ? AND fr.status IN (${terminal}) AND (fr.workflow_deploy_number IS NULL OR NOT EXISTS
      (SELECT 1 FROM workflow_deploy_history dh WHERE dh.workflow_id = fr.workflow_id AND dh.deploy_number = fr.workflow_deploy_number
       AND dh.action IN ('deploy', 'rollback', 'pull', 'migration')))
      AND (${windows.map(() => `(${started} >= ? AND ${started} < ?)`).join(' OR ')})`,
    [workflowId, ...windows.flatMap(w => [w.fromMs, w.toMs])]);
    return Number(row?.total ?? 0);
  }
}
