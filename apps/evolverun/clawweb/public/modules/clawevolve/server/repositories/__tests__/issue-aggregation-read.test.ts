import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { IssueAggregationRepository } from '../issue-aggregation-repository.js';

let db: SqliteDatabase;
let raw: Database.Database;
beforeEach(() => {
  raw = new Database(':memory:');
  db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT, workflow_id TEXT, flow_id TEXT,
    scope_type TEXT, scope_json TEXT, status TEXT, result_json TEXT,
    requested_at_ms INTEGER, completed_at_ms INTEGER, evidence_manifest_json TEXT);
    CREATE TABLE workflow_healing_diagnoses (diagnosis_id TEXT, workflow_id TEXT, flow_id TEXT,
      failure_signature TEXT, failure_mode TEXT, node_id TEXT, weak_node_id TEXT, error_text TEXT, gmt_modified INTEGER);`);
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); });

async function seed(groupCount = 8, revisions = 10, sources = 2) {
  const insert = raw.prepare(`INSERT INTO workflow_evolution_analysis_runs
    (analysis_id, workflow_id, flow_id, scope_type, scope_json, status, result_json, requested_at_ms, completed_at_ms, evidence_manifest_json)
    VALUES (?, 'wf', ?, ?, ?, ?, ?, ?, ?, ?)`);
  for (let group = 0; group < groupCount; group++) {
    for (let source = 0; source < sources; source++) {
      const id = `run-${group}-${source}`;
      insert.run(id, id, 'single_run', '{}', 'completed', JSON.stringify({
        schemaVersion: 'workflow-evolution-analysis/v1', analysisId: id, facts: [], inferences: [], unknowns: [],
        diagnoses: [{ diagnosisId: id, flowIds: [id], nodeId: `node-${group}`, failureSignature: `issue-${group}`,
          failureMode: 'timeout', severity: 'high', reasoning: 'diagnosis '.repeat(200), evidenceEventIds: [] }],
      }), 1, 1, 'unused-evidence-blob'.repeat(2000));
    }
  }
  const repository = new IssueAggregationRepository(db);
  const groups = await repository.listSources('wf');
  const snapshots = new Set<string>();
  for (let revision = 0; revision < revisions; revision++) {
    for (const group of groups) {
      const current = revision === revisions - 1;
      const snapshot = JSON.stringify({ parentAnalysisId: 'parent', input: { ...group,
        inputDigest: current ? group.inputDigest : `old-${revision}` } });
      snapshots.add(snapshot);
      insert.run(`${group.signature}-${revision}`, null, 'issue_aggregate', snapshot, 'completed', JSON.stringify({
        summary: `summary-${group.signature}-${revision}`, causes: [{ title: 'Cause', conclusion: 'Conclusion',
          certainty: 'supported', sourceIds: group.sources.map(source => source.sourceId) }], unknowns: [],
      }), revision, revision, 'unused-aggregate-evidence'.repeat(2000));
    }
  }
  return { repository, groups, snapshots };
}

describe('issue group read cost', () => {
  it('does not transfer unused evidence blobs while reading issue groups', async () => {
    const { repository } = await seed(40, 20, 6);
    const query = vi.spyOn(db, 'query');
    const start = performance.now();
    const groups = await repository.list('wf');
    const elapsedMs = Math.round(performance.now() - start);
    const batches = await Promise.all(query.mock.results.map(result => result.value));
    console.info('[issue-groups read fixture]', { groups: groups.length, snapshots: 800, elapsedMs,
      queries: batches.length, returnedBytes: batches.reduce((bytes, rows) => bytes + Buffer.byteLength(JSON.stringify(rows)), 0) });
    expect(groups).toHaveLength(40);
    expect(groups[0].sources[0].reasoning).toContain('diagnosis');
    for (const result of query.mock.results) {
      const rows = await result.value;
      expect(rows.some((row: Record<string, unknown>) => 'evidence_manifest_json' in row)).toBe(false);
    }
  });

  it('parses each historical snapshot only once, independent of the number of issue groups', async () => {
    const { repository, snapshots } = await seed();
    const parse = vi.spyOn(JSON, 'parse');
    const groups = await repository.list('wf');
    const snapshotParses = parse.mock.calls.filter(([value]) => snapshots.has(value));
    expect(groups).toHaveLength(8);
    expect(groups.every(group => group.aggregationStatus === 'completed' && !group.stale)).toBe(true);
    expect(groups.find(group => group.signature === 'issue-0')?.summary?.summary).toBe('summary-issue-0-9');
    expect(snapshotParses.length).toBeLessThanOrEqual(snapshots.size);
    expect(new Set(snapshotParses.map(([value]) => value)).size).toBe(snapshotParses.length);
  });

  it('retrieves only the displayed snapshot bodies instead of all historical bodies', async () => {
    const { repository, snapshots } = await seed();
    const query = vi.spyOn(db, 'query');
    const groups = await repository.list('wf');
    const rows = (await Promise.all(query.mock.results.map(result => result.value))).flat();
    expect(groups).toHaveLength(8);
    expect(rows.filter((row: Record<string, unknown>) => snapshots.has(String(row.scope_json)))).toHaveLength(8);
  });

  it('keeps the previous completed snapshot when the newest matching attempt failed or expired', async () => {
    const { repository } = await seed(1, 2);
    raw.prepare("UPDATE workflow_evolution_analysis_runs SET status = 'failed' WHERE analysis_id = ?").run('issue-0-1');
    let group = (await repository.list('wf'))[0];
    expect(group.aggregationStatus).toBe('failed');
    expect(group.stale).toBe(true);
    expect(group.summary?.summary).toBe('summary-issue-0-0');
    raw.prepare("UPDATE workflow_evolution_analysis_runs SET status = 'queued' WHERE analysis_id = ?").run('issue-0-1');
    group = (await repository.list('wf'))[0];
    expect(group.aggregationStatus).toBe('failed');
    expect(group.summary?.summary).toBe('summary-issue-0-0');
  });

  it('keeps the current completed revision even if a newer historical digest is completed later', async () => {
    const { repository } = await seed(1, 2);
    raw.exec(`INSERT INTO workflow_evolution_analysis_runs
      (analysis_id, workflow_id, scope_type, scope_json, status, result_json, requested_at_ms)
      SELECT 'late-old', workflow_id, scope_type, scope_json, status, result_json, requested_at_ms
      FROM workflow_evolution_analysis_runs WHERE analysis_id = 'issue-0-0'`);
    const group = (await repository.list('wf'))[0];
    expect(group.aggregationId).toBe('issue-0-1');
    expect(group.summary?.summary).toBe('summary-issue-0-1');
    expect(group.stale).toBe(false);
  });

  it('loads displayed snapshots beyond one bind-parameter batch without dropping groups', async () => {
    const { repository } = await seed(105, 1, 1);
    const groups = await repository.list('wf');
    expect(groups).toHaveLength(105);
    expect(groups.every(group => group.summary?.summary === `summary-${group.signature}-0`)).toBe(true);
  });

  it('accepts metadata arrays decoded by the MySQL driver as well as SQLite JSON text', async () => {
    const { repository } = await seed(1, 2);
    const query = db.query.bind(db);
    vi.spyOn(db, 'query').mockImplementation(async (sql, params) => {
      const rows = await query(sql, params);
      return rows.map(row => typeof row.input_key === 'string' ? { ...row, input_key: JSON.parse(row.input_key) } : row) as never;
    });
    const group = (await repository.list('wf'))[0];
    expect(group.aggregationStatus).toBe('completed');
    expect(group.summary?.summary).toBe('summary-issue-0-1');
  });
});
