import type Database from 'better-sqlite3';
import { DatabaseSync } from 'node:sqlite';
import { SqliteDatabase } from '../../../../../shared/server/db.js';
import { migrations } from '../../../../../shared/server/schema.js';
import { digestRepairJson, type RepairItem } from '../../contracts/repair-batch.js';
import type { RepairExecutionPort, RepairGeneratedResult, RepairSourcePort } from '../../contracts/repair-workbench.js';

export async function repairFixture(count = 2) {
  const raw = new DatabaseSync(':memory:');
  const db = new SqliteDatabase(raw as unknown as Database.Database);
  raw.exec(`CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY); INSERT INTO workflow_specs VALUES ('wf-1'), ('wf-2');`);
  const legacy = migrations.flatMap(m => m.sql).find(sql => sql.startsWith('CREATE TABLE IF NOT EXISTS workflow_healing_outcomes ('))!;
  await db.exec(db.dialect.renderDdl(legacy));
  for (const m of migrations.filter(m => [72, 117, 122, 123].includes(m.version))) for (const sql of m.sql) await db.exec(db.dialect.renderDdl(sql));
  const items: RepairItem[] = Array.from({ length: count }, (_, i) => ({ itemId: `item-${i}`, groupKey: `group-${i}`,
    proposalKey: digestRepairJson({ proposal: i }), contentRevision: 1, previousItemId: null,
    proposal: { summary: `Fix problem ${i}` }, instruction: '', sources: [{ kind: 'suggestion', suggestionId: `suggestion-${i}`,
      proposalDigest: digestRepairJson({ proposal: i }), instructionDigest: digestRepairJson('') }] }));
  const sourcePort: RepairSourcePort = { async load(_tx, workflowId) { return workflowId === 'wf-1' ? items.map(item => ({ item, episodeKey: 'initial' })) : []; } };
  const calls: unknown[] = [];
  const stops: unknown[] = [];
  let remoteStatus: Awaited<ReturnType<RepairExecutionPort['status']>> = { status: 'running', rawStatus: 'running', errorMessage: null };
  let generatedResult: RepairGeneratedResult | null = null;
  const executionPort: RepairExecutionPort = {
    async resolveBaseline(workflowId) { return { workflowId, packId: 'pack', releaseRevision: 0, activeDeployNumber: null,
      repoId: 'managed', specDigest: 'a'.repeat(64), specPath: 'workflows/wf-1.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) }; },
    async dispatch(input) { calls.push(input); return { jobId: 'job-1' }; },
    async status() { return remoteStatus; },
    async readResult() {
      if (!generatedResult) throw new Error('result not ready');
      return generatedResult;
    },
    async stop(input) { stops.push(input); },
    async diff(input) { calls.push(input); return { baseCommit: input.baseCommit, candidateCommit: input.candidateCommit, files: [], truncated: false }; },
  };
  return { raw, db, items, sourcePort, executionPort, calls, stops,
    setRemoteStatus(value: typeof remoteStatus) { remoteStatus = value; },
    setGeneratedResult(value: RepairGeneratedResult) { generatedResult = value; } };
}
