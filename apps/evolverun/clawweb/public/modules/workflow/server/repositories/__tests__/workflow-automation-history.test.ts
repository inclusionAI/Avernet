// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { WorkflowAutomationHistoryRepository } from '../workflow-automation-history-repository.js';
import { taskScope } from '../workflow-automation-repository.js';
import { automationRecordQuery } from '../../contracts/workflow-automation-history.js';
let raw: Database.Database, db: SqliteDatabase, repo: WorkflowAutomationHistoryRepository;
const now = Date.parse('2026-10-20T10:00:00+08:00'), day = 86400000;
beforeEach(async () => {
  raw = new Database(':memory:'); db = new SqliteDatabase(raw);
  for (const migration of repairReleaseMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
  repo = new WorkflowAutomationHistoryRepository(db);
});
afterEach(async () => { await db.close(); });
function insert(i: number, time: number, kind = 'auto_run', status = 'done', workflow = 'wf', complete: boolean | undefined = true) {
  const id = i.toString(16).padStart(64, '0');
  raw.prepare(`INSERT INTO workflow_repair_release_records (record_id, workflow_id, task_id, revision, record_kind,
    state_version, request_key, request_digest, body_json, created_at_ms, updated_at_ms) VALUES (?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?)`)
    .run(id, workflow, taskScope(workflow), kind, id, id, JSON.stringify({ status, attempts: 2, reason: complete === false ? 'PARTIAL_ANALYSIS' : null,
      data: { flowId: `flow-${i}`, analysisId: `analysis-${i}`, complete, request: { privateModelInput: 'must not return full journals' } } }), time, now + i);
  return id;
}
it('pages across all three kinds beyond fifty records, with equal timestamps and workflow isolation', async () => {
  for (let i = 1; i <= 65; i++) insert(i, now - Math.floor(i / 5), ['auto_run', 'auto_draft', 'auto_notice'][i % 3], i % 2 ? 'done' : 'waiting');
  insert(100, now, 'auto_cycle'); insert(101, now, 'proposal_group'); insert(102, now, 'auto_run', 'done', 'other');
  const first = await repo.page('wf', {}, now);
  expect(first.jobs).toHaveLength(8);
  const ids = [...first.jobs.map(job => job.id)]; let cursor = first.nextCursor;
  // A new record inserted after the first page cannot shift forward pages.
  insert(103, now + 1);
  while (cursor) { const page = await repo.page('wf', { limit: 8, cursor }, now + 1000); ids.push(...page.jobs.map(job => job.id)); cursor = page.nextCursor; }
  expect(ids).toHaveLength(65); expect(new Set(ids).size).toBe(65);
  expect(ids).toEqual(raw.prepare("SELECT record_id FROM workflow_repair_release_records WHERE workflow_id='wf' AND record_kind IN ('auto_run','auto_draft','auto_notice') AND created_at_ms<=? ORDER BY created_at_ms DESC,record_id DESC").all(now).map((row: any) => row.record_id));
  expect(first.jobs[0]).not.toHaveProperty('data'); expect(first.jobs[0]).not.toHaveProperty('body_json');
});
it('filters creation dates by Shanghai calendar days and status in SQL, preserving partial analysis', async () => {
  const midnight = Date.parse('2026-10-20T00:00:00+08:00');
  insert(1, midnight, 'auto_run', 'waiting'); insert(2, midnight - 1, 'auto_run', 'done', 'wf', false);
  insert(3, midnight - 6 * day); insert(4, midnight - 6 * day - 1); insert(5, now + 1);
  expect((await repo.page('wf', { period: 'today' }, now)).jobs.map(job => job.flowId)).toEqual(['flow-1']);
  const week = await repo.page('wf', { period: '7d', status: 'done' }, now);
  expect(week.jobs.map(job => job.flowId)).toEqual(['flow-2', 'flow-3']);
  expect(week.jobs[0]).toMatchObject({ complete: false, reason: 'PARTIAL_ANALYSIS' });
  expect((await repo.page('wf', { status: 'blocked' }, now)).jobs).toEqual([]);
});
it('rejects oversized pages, invalid filters and invalid cursors before any database read', async () => {
  const read = vi.spyOn(db, 'query');
  for (const input of [{ limit: 1000 }, { limit: 0 }, { period: 'yesterday' }, { status: "done' OR 1=1" }, { cursor: 'invalid' }, { cursor: Buffer.from('{"at":1,"time":1,"id":"x"}').toString('base64url') }]) {
    await expect(repo.page('wf', input as never, now)).rejects.toThrow('执行记录筛选或分页参数无效');
  }
  expect(read).not.toHaveBeenCalled();
  expect(() => automationRecordQuery({ limit: ['8', '20'] })).toThrow();
});
it('uses the existing task index and bounded projections without truncating rare-status or deep history', async () => {
  raw.transaction(() => { for (let i = 1; i <= 20000; i++) insert(i, now - i, ['auto_run', 'auto_draft', 'auto_notice'][i % 3], i > 19990 ? 'blocked' : 'done'); })();
  const query = vi.spyOn(db, 'query');
  for (const status of ['all', 'blocked'] as const) {
    query.mockClear();
    const page = await repo.page('wf', { limit: 8, status }, now);
    expect(page.jobs).toHaveLength(8); expect(query).toHaveBeenCalledTimes(1);
    for (const [sql, args] of query.mock.calls) {
      expect(sql).not.toMatch(/COUNT\s*\(|OFFSET|SELECT\s+body_json/i);
      expect(args?.at(-1)).toBe(9);
      const plans = raw.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args! as never[]) as { detail: string }[];
      expect(plans.some(row => row.detail.includes('idx_repair_release_task'))).toBe(true);
      expect(plans.some(row => /SCAN workflow_repair_release_records/.test(row.detail))).toBe(false);
    }
    query.mockClear();
    const deepCursor = Buffer.from(JSON.stringify({at:now,time:now-15000,id:(15000).toString(16).padStart(64,'0')})).toString('base64url');
    const deep = await repo.page('wf', { limit: 8, status, cursor: deepCursor }, now);
    expect(deep.jobs.every(job => job.createdAtMs! <= now-15000)).toBe(true);
    for (const [sql, args] of query.mock.calls) {
      const plans = raw.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args! as never[]) as { detail: string }[];
      expect(plans.some(row => row.detail.includes('idx_repair_release_task'))).toBe(true);
      expect(args).toContain(now-15000);
    }
  }
});
it('reads current status from historical journals without a generated column', async () => {
  const id = insert(1, now, 'auto_run', 'waiting');
  expect((await repo.page('wf', { status: 'waiting' }, now)).jobs).toHaveLength(1);
  raw.prepare("UPDATE workflow_repair_release_records SET body_json = json_set(body_json, '$.status', 'done') WHERE record_id=?").run(id);
  expect((await repo.page('wf', { status: 'waiting' }, now)).jobs).toEqual([]);
  expect((await repo.page('wf', { status: 'done' }, now)).jobs).toHaveLength(1);
});

it('extracts display JSON only after selecting a page, even when newer rows arrive last in index order', async () => {
  for (let i = 1; i <= 200; i++) insert(i, now - 200 + i);
  let extractions = 0;
  raw.function('observe_json', value => { extractions++; return value; });
  const query = db.query.bind(db);
  vi.spyOn(db, 'query').mockImplementation((sql, args) => query(sql.replace(/JSON_EXTRACT\(([^)]+)\)/g, 'observe_json(JSON_EXTRACT($1))'), args));
  const page = await repo.page('wf', { limit: 8 }, now);
  expect(page.jobs.map(row => row.flowId)).toEqual(['flow-200', 'flow-199', 'flow-198', 'flow-197', 'flow-196', 'flow-195', 'flow-194', 'flow-193']);
  expect(extractions).toBeLessThanOrEqual(9 * 8);
});
