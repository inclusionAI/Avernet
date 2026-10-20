import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { AutomationJobSummary, AutomationRecordPage, AutomationRecordQuery } from '../contracts/workflow-automation.js';
import { automationRecordQuery, InvalidAutomationRecordQuery } from '../contracts/workflow-automation-history.js';
import { taskScope } from './workflow-automation-repository.js';
const DAY = 86400000;
type Cursor = { at: number; time: number; id: string };
export class WorkflowAutomationHistoryRepository {
  constructor(private readonly db: IDatabase) {}
  async page(workflowId: string, input: AutomationRecordQuery = {}, now = Date.now()): Promise<AutomationRecordPage> {
    const query = automationRecordQuery(input);
    let cursor: Cursor | undefined;
    if (query.cursor) {
      try { cursor = JSON.parse(Buffer.from(query.cursor, 'base64url').toString('utf8')); }
      catch { throw new InvalidAutomationRecordQuery(); }
      if (!cursor || !Number.isSafeInteger(cursor.at) || cursor.at < 0 || cursor.at > now || !Number.isSafeInteger(cursor.time)
        || cursor.time < 0 || cursor.time > cursor.at || typeof cursor.id !== 'string' || !/^[a-f0-9]{64}$/.test(cursor.id)) throw new InvalidAutomationRecordQuery();
    }
    const at = cursor?.at ?? now;
    const midnight = Math.floor((at + 8 * 3600000) / DAY) * DAY - 8 * 3600000;
    const from = query.period === 'today' ? midnight : query.period === '7d' ? midnight - 6 * DAY : 0;
    // One ordered index range per kind: IN + ORDER BY would otherwise sort the complete workflow history.
    const rows: AutomationJobSummary[] = [];
    for (const kind of ['auto_run', 'auto_draft', 'auto_notice']) {
      const where = ['task_id = ?', 'record_kind = ?'];
      const params: unknown[] = [taskScope(workflowId), kind];
      if (query.status !== 'all') { where.push('record_status = ?'); params.push(query.status); }
      where.push('created_at_ms >= ?', 'created_at_ms <= ?'); params.push(from, cursor?.time ?? at);
      if (cursor) { where.push('(created_at_ms < ? OR (created_at_ms = ? AND record_id < ?))'); params.push(cursor.time, cursor.time, cursor.id); }
      const field = (name: string) => this.db.dialect.driver === 'better-sqlite3'
        ? `JSON_EXTRACT(body_json, '$.${name}')` : `JSON_UNQUOTE(JSON_EXTRACT(body_json, '$.${name}'))`;
      const page = await this.db.query<AutomationJobSummary & { realTaskId?: string }>(`SELECT record_id AS id, record_kind AS kind,
        record_status AS status, created_at_ms AS createdAtMs, ${field('attempts')} AS attempts, ${field('reason')} AS reason,
        ${field('data.flowId')} AS flowId, ${field('data.analysisId')} AS analysisId, ${field('data.complete')} AS complete,
        ${field('data.realTaskId')} AS realTaskId, ${field('data.taskId')} AS taskId
        FROM workflow_repair_release_records WHERE ${where.join(' AND ')}
        ORDER BY created_at_ms DESC, record_id DESC LIMIT ?`, [...params, query.limit! + 1]);
      for (const row of page) {
        const text = (v: unknown) => typeof v === 'string' && v !== 'null' && v ? v : undefined;
        const complete = row.complete as unknown;
        rows.push({ id: row.id, kind: row.kind, status: row.status, createdAtMs: Number(row.createdAtMs), attempts: Number(row.attempts ?? 0),
          reason: text(row.reason) ?? null, flowId: text(row.flowId), analysisId: text(row.analysisId), taskId: text(row.realTaskId) ?? text(row.taskId),
          ...(new Set<unknown>([true, false, 0, 1, 'true', 'false', '0', '1']).has(complete) ? { complete: new Set<unknown>([true, 1, 'true', '1']).has(complete) } : {}) });
      }
    }
    rows.sort((a, b) => b.createdAtMs! - a.createdAtMs! || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
    const jobs = rows.slice(0, query.limit), last = jobs.at(-1);
    return { jobs, nextCursor: rows.length > query.limit! && last ? Buffer.from(JSON.stringify({ at, time: last.createdAtMs, id: last.id })).toString('base64url') : null };
  }
}
