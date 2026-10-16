// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { fillRepairSampleAnalyses } from '../repair-sample-analyses.js';

const databases: SqliteDatabase[] = [];
afterEach(async () => { for (const db of databases.splice(0)) await db.close(); });
function fixture() {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw); databases.push(db);
  raw.exec(`CREATE TABLE workflow_evolution_analysis_runs (id INTEGER PRIMARY KEY, workflow_id TEXT,
    status TEXT, scope_type TEXT, flow_id TEXT, scope_json TEXT, result_json TEXT, completed_at_ms INTEGER, requested_at_ms INTEGER DEFAULT 0)`);
  const insert = raw.prepare(`INSERT INTO workflow_evolution_analysis_runs
    (workflow_id, status, scope_type, flow_id, scope_json, result_json, completed_at_ms)
    VALUES ('wf', 'completed', 'run', ?, ?, ?, ?)`);
  const result = (extra: object = {}) => ({ schemaVersion: 'workflow-evolution-analysis/v1', analysisId: 'analysis',
    facts: [], inferences: [], unknowns: [], diagnoses: [], ...extra });
  return { db, raw, result, insert(flowId: string | null, scope: object, body: object | string, time: number | null = 1) {
    insert.run(flowId, JSON.stringify(scope), typeof body === 'string' ? body : JSON.stringify(body), time);
  } };
}

it('uses requested time for a newer completed analysis with no completion timestamp', async () => {
  const f = fixture();
  f.insert('run', {}, f.result(), 10);
  f.insert('run', { nodeId: 'n' }, f.result({ unknowns: ['Incomplete latest evidence'] }), null);
  f.raw.exec('UPDATE workflow_evolution_analysis_runs SET requested_at_ms = 20 WHERE completed_at_ms IS NULL');
  const samples = new Map([['run', { analysisComplete: false, signatures: [] as string[] }]]);
  await fillRepairSampleAnalyses(f.db, 'wf', 'n', samples);
  expect(samples.get('run')?.analysisComplete).toBe(false);
});

it('matches scope/diagnosis flow IDs, pages past node-specific analyses, and fences invalid latest evidence', async () => {
  const f = fixture();
  const samples = new Map(['scoped', 'diagnosed', 'partial', 'invalid'].map(id => [id, { analysisComplete: false, signatures: [] as string[] }]));
  f.insert(null, { flowIds: ['scoped'] }, f.result());
  f.insert(null, {}, f.result({ diagnoses: [{ diagnosisId: 'diag', flowIds: ['diagnosed'], nodeId: 'n',
    failureSignature: 'timeout:n', failureMode: 'timeout', severity: 'high', reasoning: 'observed', evidenceEventIds: [] }] }));
  f.insert('partial', {}, f.result(), null);
  f.insert('invalid', {}, f.result());
  f.insert('invalid', {}, 'invalid JSON');
  for (let i = 0; i < 250; i++) f.insert('partial', { nodeId: 'other' }, f.result());
  await fillRepairSampleAnalyses(f.db, 'wf', 'n', samples);
  expect(samples.get('scoped')?.analysisComplete).toBe(true);
  expect(samples.get('diagnosed')).toEqual({ analysisComplete: true, signatures: ['timeout:n'] });
  expect(samples.get('partial')?.analysisComplete).toBe(true);
  expect(samples.get('invalid')?.analysisComplete).toBe(false);
});

it('does not let a cross-batch analysis override newer evidence in the next sample batch', async () => {
  const f = fixture();
  const ids = Array.from({ length: 101 }, (_, i) => `run-${i}`);
  const samples = new Map(ids.map(id => [id, { analysisComplete: false, signatures: [] as string[] }]));
  f.insert(null, { flowIds: ids }, f.result());
  f.insert(ids[100], {}, f.result({ unknowns: ['latest evidence is incomplete'] }));
  await fillRepairSampleAnalyses(f.db, 'wf', 'n', samples);
  expect([...samples.values()].filter(sample => sample.analysisComplete)).toHaveLength(100);
  expect(samples.get(ids[100])?.analysisComplete).toBe(false);
});
