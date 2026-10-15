import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, boundedRepairJson, digestRepairJson, MAX_RESULT_BYTES } from '../contracts/repair-batch.js';
type ReleaseRecord = { id: string; workflowId: string; taskId: string; revision: number; stateVersion: number; createdAtMs: number };

export class RepairReleaseRepository {
  constructor(private readonly db: IDatabase) {}
  async list<T>(taskId: string, kind: string): Promise<T[]> {
    const rows = await this.db.query<{ body_json: string }>(
      'SELECT body_json FROM workflow_repair_release_records WHERE task_id = ? AND record_kind = ? ORDER BY created_at_ms DESC, state_version DESC', [taskId, kind]);
    return rows.map(row => JSON.parse(row.body_json) as T);
  }
  async retry<T>(taskId: string, kind: string, request: object): Promise<T | null> {
    const key = digestRepairJson([taskId, kind, (request as { requestId: string }).requestId]);
    const rows = await this.db.query<{ body_json: string; request_digest: string }>(
      'SELECT body_json, request_digest FROM workflow_repair_release_records WHERE request_key = ?', [key]);
    if (!rows.length) return null;
    if (rows[0].request_digest !== digestRepairJson(request)) throw new RepairBatchError('IDEMPOTENCY_MISMATCH', '请求编号已用于不同的操作内容');
    return JSON.parse(rows[0].body_json) as T;
  }
  async insert<T extends ReleaseRecord>(kind: string, request: object, value: T): Promise<void> {
    await this.db.exec(`INSERT INTO workflow_repair_release_records
      (record_id, workflow_id, task_id, revision, record_kind, state_version, request_key, request_digest, body_json, created_at_ms, updated_at_ms)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, [value.id, value.workflowId, value.taskId, value.revision, kind, value.stateVersion,
      digestRepairJson([value.taskId, kind, (request as { requestId: string }).requestId]), digestRepairJson(request),
      boundedRepairJson(value, MAX_RESULT_BYTES), value.createdAtMs, Date.now()]);
  }
  async update(value: ReleaseRecord, expected: number): Promise<void> {
    const result = await this.db.exec(`UPDATE workflow_repair_release_records SET body_json = ?, state_version = ?, updated_at_ms = ?
      WHERE record_id = ? AND state_version = ?`, [boundedRepairJson(value, MAX_RESULT_BYTES), value.stateVersion, Date.now(), value.id, expected]);
    if (result.affectedRows !== 1) throw new RepairBatchError('STATE_CONFLICT', '部署状态已变化，请刷新后核对');
  }
}
