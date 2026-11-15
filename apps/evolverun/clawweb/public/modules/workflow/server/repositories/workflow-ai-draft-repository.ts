import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { WorkflowAiDraftEnvelope, WorkflowAiDraftStatus } from '../contracts/workflow-ai-draft.js';
export type AiDraftRow = {
  draft_id: string; request_key: string; request_digest: string; workflow_id: string; actor_id: string;
  envelope_json: string; status: WorkflowAiDraftStatus; job_id: string | null; error_code: string | null;
  result_json: string | null; state_version: number; created_at_ms: number; updated_at_ms: number; next_poll_at_ms: number;
};
export class WorkflowAiDraftRepository {
  constructor(private readonly db: IDatabase) {}
  async get(draftId: string): Promise<AiDraftRow | null> {
    const rows = await this.db.query<AiDraftRow>('SELECT * FROM workflow_ai_drafts WHERE draft_id = ?', [draftId]);
    return rows.length === 1 && rows[0].draft_id === draftId ? rows[0] : null;
  }
  async request(key: string): Promise<AiDraftRow | null> {
    const rows = await this.db.query<AiDraftRow>('SELECT * FROM workflow_ai_drafts WHERE request_key = ?', [key]);
    return rows[0] ?? null;
  }
  async insert(input: { requestKey: string; digest: string; actorId: string; envelope: WorkflowAiDraftEnvelope; now: number }): Promise<AiDraftRow> {
    const { envelope, now } = input;
    await this.db.exec(`INSERT INTO workflow_ai_drafts
      (draft_id, request_key, request_digest, workflow_id, actor_id, envelope_json, status, state_version,
       created_at_ms, updated_at_ms, next_poll_at_ms, gmt_create, gmt_modified)
      VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)`,
    [envelope.taskId, input.requestKey, input.digest, envelope.input.workflowId, input.actorId, JSON.stringify(envelope), now, now, now, this.db.dialect.now(), this.db.dialect.now()]);
    const row = await this.get(envelope.taskId);
    if (!row) throw new Error('AI_DRAFT_STORAGE_ERROR');
    return row;
  }
  async update(row: AiDraftRow, patch: Partial<Pick<AiDraftRow, 'status' | 'job_id' | 'error_code' | 'result_json' | 'next_poll_at_ms'>>, now: number): Promise<boolean> {
    const fields = Object.keys(patch) as Array<keyof typeof patch>;
    const result = await this.db.exec(`UPDATE workflow_ai_drafts SET ${fields.map(field => `${field} = ?`).join(', ')},
      state_version = state_version + 1, updated_at_ms = ?, gmt_modified = ? WHERE draft_id = ? AND state_version = ?`,
    [...fields.map(field => patch[field]), now, this.db.dialect.now(), row.draft_id, row.state_version]);
    if (![0, 1].includes(result.affectedRows)) throw new Error('AI_DRAFT_STORAGE_ERROR');
    return result.affectedRows === 1;
  }
  // Poll admission advances only the clock; it must not invalidate an in-flight
  // business result. Cancel/ack/terminal updates still advance state_version.
  async claimDue(row: AiDraftRow, now: number, nextPollAt: number): Promise<boolean> {
    const result = await this.db.exec(`UPDATE workflow_ai_drafts SET next_poll_at_ms = ?,
      updated_at_ms = ?, gmt_modified = ? WHERE draft_id = ? AND state_version = ? AND next_poll_at_ms <= ?
      AND status IN ('running', 'dispatch_uncertain')`,
    [nextPollAt, now, this.db.dialect.now(), row.draft_id, row.state_version, now]);
    if (![0, 1].includes(result.affectedRows)) throw new Error('AI_DRAFT_STORAGE_ERROR');
    return result.affectedRows === 1;
  }
  async due(now: number, limit: number): Promise<AiDraftRow[]> {
    return this.db.query<AiDraftRow>(`SELECT * FROM workflow_ai_drafts WHERE status IN ('pending', 'running', 'dispatch_uncertain')
      AND next_poll_at_ms <= ? ORDER BY next_poll_at_ms, created_at_ms LIMIT ?`, [now, limit]);
  }
}
