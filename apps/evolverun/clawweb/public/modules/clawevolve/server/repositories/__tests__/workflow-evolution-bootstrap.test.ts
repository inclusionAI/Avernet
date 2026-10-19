// @vitest-environment node
import Database from 'better-sqlite3';
import { expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { repairBatchMigrations } from '@avernet/clawweb-shared/server/schema-repair-batch';
import { WorkflowEvolutionRepository } from '../workflow-evolution-repository.js';

it('reads and completes analyses using only the bootstrapped suggestion schema', async () => {
  const db = new SqliteDatabase(new Database(':memory:'));
  try {
    await repairBatchMigrations[0].migrate!(db);
    for (const sql of repairBatchMigrations[0].sql) await db.exec(db.dialect.renderDdl(sql));
    // Analysis storage is provisioned separately; do not supplement suggestion columns here.
    await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT UNIQUE, request_key TEXT UNIQUE,
      scope_type TEXT, scope_json TEXT, workflow_id TEXT, status TEXT, analysis_version TEXT,
      evidence_manifest_json TEXT, requested_by TEXT, requested_at_ms INTEGER, result_json TEXT,
      result_digest TEXT, diagnosis_count INTEGER DEFAULT 0, error_code TEXT, completed_at_ms INTEGER,
      state_version INTEGER DEFAULT 0, gmt_create INTEGER, gmt_modified INTEGER
    )`);
    await db.exec(`INSERT INTO workflow_evolution_analysis_runs
      (analysis_id, request_key, scope_type, scope_json, workflow_id, status, analysis_version, requested_at_ms)
      VALUES ('analysis', 'request', 'workflow_window', '{}', 'wf', 'analyzing', 'v1', 100)`);
    const repo = new WorkflowEvolutionRepository(db);
    expect((await repo.getAnalysisInput('analysis'))?.history.suggestions).toEqual([]);
    const proposal = { schemaVersion: 'workflow-patch/v1', workflowId: 'wf', baseSpecDigest: 'a'.repeat(64),
      summary: 'Raise timeout', operations: [{ op: 'replace', nodeId: 'fetch', path: '/executor/timeoutMs', value: 200 }] };
    await repo.completeAnalysisRun('analysis', {
      schemaVersion: 'workflow-evolution-analysis/v1', analysisId: 'analysis', facts: [], inferences: [], unknowns: [],
      diagnoses: [{ diagnosisId: 'diagnosis', flowIds: ['flow'], nodeId: 'fetch', failureSignature: 'timeout',
        failureMode: 'timeout', severity: 'high', reasoning: 'Timeout too short', evidenceEventIds: [], proposal }],
    }, 200);
    expect(await db.query('SELECT proposal_json, proposal_digest, apply_task_id FROM workflow_healing_suggestions'))
      .toEqual([{ proposal_json: expect.any(String), proposal_digest: expect.stringMatching(/^[a-f0-9]{64}$/), apply_task_id: null }]);
    expect((await repo.getAnalysisInput('analysis'))?.history.suggestions[0].proposal).toEqual(proposal);
    // Exercise the existing-suggestion UPDATE path as well as the initial INSERT.
    await db.exec("UPDATE workflow_evolution_analysis_runs SET status = 'analyzing' WHERE analysis_id = 'analysis'");
    await repo.completeAnalysisRun('analysis', {
      schemaVersion: 'workflow-evolution-analysis/v1', analysisId: 'analysis', facts: [], inferences: [], unknowns: [],
      diagnoses: [{ diagnosisId: 'diagnosis-2', flowIds: ['flow-2'], nodeId: 'fetch', failureSignature: 'timeout',
        failureMode: 'timeout', severity: 'high', reasoning: 'Timeout still short', evidenceEventIds: [], proposal }],
    }, 300);
    expect(await db.query('SELECT COUNT(*) AS n FROM workflow_healing_suggestions')).toEqual([{ n: 1 }]);
  } finally { await db.close(); }
});
