// @vitest-environment node
import Database from 'better-sqlite3';
import { afterEach, describe, expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowEvolutionRepository } from '../workflow-evolution-repository.js';
const opened: SqliteDatabase[] = [];
afterEach(async () => { for (const db of opened.splice(0)) await db.close(); });
async function setup() {
  const db = new SqliteDatabase(new Database(':memory:')); opened.push(db);
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,analysis_id TEXT UNIQUE,request_key TEXT UNIQUE,scope_type TEXT,scope_json TEXT,
    flow_id TEXT,workflow_id TEXT,status TEXT,evidence_status TEXT,evidence_snapshot_ref TEXT,evidence_snapshot_digest TEXT,
    evidence_manifest_json TEXT,task_id TEXT,step_id TEXT,analysis_version TEXT,requested_by TEXT,requested_at_ms INTEGER,
    state_version INTEGER DEFAULT 0,gmt_create INTEGER,gmt_modified INTEGER)`);
  await db.exec(`CREATE TABLE workflow_run_evidence_events (id INTEGER,event_id TEXT,flow_id TEXT,workflow_id TEXT,
    producer TEXT,event_seq INTEGER,event_type TEXT,occurred_at_ms INTEGER,payload_json TEXT)`);
  await db.exec('CREATE TABLE flow_runs (flow_id TEXT,workflow_id TEXT,workflow_version INTEGER,workflow_deploy_number INTEGER,state_json TEXT)');
  await db.exec('CREATE TABLE workflow_deploy_history (workflow_id TEXT,version INTEGER,deploy_number INTEGER,spec_json TEXT,is_active INTEGER)');
  await db.exec('CREATE TABLE workflow_specs (workflow_id TEXT,spec_json TEXT)');
  await db.exec("INSERT INTO flow_runs VALUES ('run','wf',39,34,NULL)");
  await db.exec("INSERT INTO workflow_deploy_history VALUES ('wf',39,34,?,1)",[JSON.stringify({ id: 'wf', nodes: [{ id:'fetch',timeoutMs:20 }] })]);
  await db.exec("INSERT INTO workflow_specs VALUES ('wf',?)",[JSON.stringify({ id:'wf', nodes:[{id:'fetch',timeoutMs:999}] })]);
  const repo = new WorkflowEvolutionRepository(db);
  const create = () => repo.createAnalysisRun({ analysisId:'analysis',requestKey:'request',scopeType:'single_run',
    scope:{flowIds:['run']},flowId:'run',workflowId:'wf',analysisVersion:'v1',requestedAtMs:100 });
  return {db,repo,create};
}
describe('analysis source configuration', () => {
  it('freezes the run source at creation and never reads subsequently saved or deployed configuration', async () => {
    const {db,repo,create}=await setup();
    await create();
    await db.exec('UPDATE workflow_deploy_history SET is_active=0');
    await db.exec("INSERT INTO workflow_deploy_history VALUES ('wf',40,35,?,1)",[JSON.stringify({id:'wf',nodes:[]})]);
    const input=await repo.getWorkflowSnapshotInput('analysis');
    expect(input.workflowSpec).toEqual({id:'wf',nodes:[{id:'fetch',timeoutMs:20}]});
    expect(input.workflowSnapshot).toMatchObject({workflowVersion:39,deployNumber:34});
    await create();
    expect(await repo.getWorkflowSnapshotInput('analysis')).toEqual(input);
    const row=await repo.findAnalysisRun('analysis');
    expect(JSON.parse(row!.scope_json).workflowSnapshot).toMatchObject({status:'resolved',snapshot:{workflowVersion:39}});
  });
  it('does not substitute latest configuration for a missing source snapshot', async () => {
    const {db,repo,create}=await setup();
    await db.exec('DELETE FROM workflow_deploy_history');
    await create();
    await expect(repo.getWorkflowSnapshotInput('analysis')).rejects.toThrow('WORKFLOW_SNAPSHOT_UNAVAILABLE');
  });
  it('does not supply a single configuration for a set of runs using different versions', async () => {
    const {db,repo}=await setup();
    await db.exec("INSERT INTO workflow_deploy_history VALUES ('wf',40,35,?,0)",[JSON.stringify({id:'wf',nodes:[]})]);
    await db.exec("INSERT INTO flow_runs VALUES ('other','wf',40,35,NULL)");
    await repo.createAnalysisRun({analysisId:'mixed',requestKey:'mixed',scopeType:'run_set',scope:{flowIds:['run','other']},workflowId:'wf',analysisVersion:'v1',requestedAtMs:100});
    await expect(repo.getWorkflowSnapshotInput('mixed')).rejects.toThrow('WORKFLOW_SNAPSHOT_UNAVAILABLE');
  });
});
