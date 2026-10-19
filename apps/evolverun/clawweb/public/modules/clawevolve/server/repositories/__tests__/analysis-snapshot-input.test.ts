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
  it.each([false, true])('resolves and freezes a pre-rollout queued analysis from run scope (snapshot column: %s)', async snapshotColumn => {
    const {db,repo,create}=await setup();
    await create();
    if (snapshotColumn) await db.exec('ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN workflow_snapshot_json TEXT');
    await db.exec('UPDATE workflow_evolution_analysis_runs SET scope_json = ?', [JSON.stringify({ flowIds:['run'], extra:'preserved' })]);
    const input=await repo.getWorkflowSnapshotInput('analysis');
    expect(input.workflowSnapshot).toMatchObject({kind:'deployment',workflowVersion:39,deployNumber:34});
    const row=(await repo.findAnalysisRun('analysis'))!;
    expect(JSON.parse(row.scope_json)).toMatchObject({flowIds:['run'],extra:'preserved',workflowSnapshot:{status:'resolved',snapshot:input.workflowSnapshot}});
    if(snapshotColumn) expect(JSON.parse(row.workflow_snapshot_json!)).toEqual(JSON.parse(row.scope_json).workflowSnapshot);
    // Once frozen, a changed source record cannot silently rebind an accepted analysis.
    await db.exec("UPDATE flow_runs SET workflow_version=40,workflow_deploy_number=35");
    await expect(repo.getWorkflowSnapshotInput('analysis')).resolves.toEqual(input);
  });
  it('resolves a pre-rollout saved-snapshot analysis using its single flow id', async () => {
    const {db,repo,create}=await setup();
    await create();
    await db.exec('UPDATE workflow_evolution_analysis_runs SET scope_json = ?', ['{}']);
    const spec={id:'wf',nodes:[{id:'fetch',timeoutMs:42}]};
    await db.exec('UPDATE flow_runs SET workflow_version=-1,state_json=?', [JSON.stringify({workflowPin:{source:'remote-snapshot',workflowId:'wf',workflowDigest:`remote-snapshot:${'a'.repeat(40)}`},workflowSnapshot:spec})]);
    const input=await repo.getWorkflowSnapshotInput('analysis');
    expect(input.workflowSpec).toEqual(spec);
    expect(input.workflowSnapshot).toMatchObject({kind:'saved',commit:'a'.repeat(40),sourceFlowId:'run'});
  });
  it('does not rebind an explicitly frozen unresolved analysis after its run becomes resolvable', async () => {
    const {db,repo,create}=await setup();
    await db.exec('UPDATE flow_runs SET workflow_version=NULL,workflow_deploy_number=NULL');
    await create();
    await db.exec('UPDATE flow_runs SET workflow_version=39,workflow_deploy_number=34');
    await expect(repo.getWorkflowSnapshotInput('analysis')).rejects.toThrow('WORKFLOW_SNAPSHOT_UNAVAILABLE');
  });
  it('does not report legacy analysis input success when freezing its association fails', async () => {
    const {db,repo,create}=await setup();
    await create();
    await db.exec('UPDATE workflow_evolution_analysis_runs SET scope_json = ?', ['{"flowIds":["run"]}']);
    await db.exec("CREATE TRIGGER reject_binding BEFORE UPDATE OF scope_json ON workflow_evolution_analysis_runs BEGIN SELECT RAISE(FAIL,'binding write failed'); END");
    await expect(repo.getWorkflowSnapshotInput('analysis')).rejects.toThrow('binding write failed');
    expect(JSON.parse((await repo.findAnalysisRun('analysis'))!.scope_json)).toEqual({flowIds:['run']});
  });

  it('rejects a legacy mixed-version scope rather than choosing one available version', async () => {
    const {db,repo,create}=await setup();
    await create();
    await db.exec("INSERT INTO workflow_deploy_history VALUES ('wf',40,35,?,0)",[JSON.stringify({id:'wf',nodes:[]})]);
    await db.exec("INSERT INTO flow_runs VALUES ('other','wf',40,35,NULL)");
    await db.exec('UPDATE workflow_evolution_analysis_runs SET scope_json = ?', ['{"flowIds":["run","other"]}']);
    await expect(repo.getWorkflowSnapshotInput('analysis')).rejects.toThrow('WORKFLOW_SNAPSHOT_UNAVAILABLE');
    expect(JSON.parse((await repo.findAnalysisRun('analysis'))!.scope_json).workflowSnapshot).toEqual({status:'unresolved',reason:'IDENTITY_MISMATCH'});
  });

});
