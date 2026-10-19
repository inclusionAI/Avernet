import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { IssueAggregationRepository } from '../issue-aggregation-repository.js';

let db: SqliteDatabase;
let raw: Database.Database;
beforeEach(() => {
  raw = new Database(':memory:');
  raw.exec("CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT, workflow_id TEXT)");
  db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT, workflow_id TEXT, flow_id TEXT,
    scope_type TEXT, scope_json TEXT, status TEXT, result_json TEXT,
    requested_at_ms INTEGER, completed_at_ms INTEGER, evidence_manifest_json TEXT);
    CREATE TABLE workflow_healing_diagnoses (diagnosis_id TEXT, workflow_id TEXT, flow_id TEXT,
      failure_signature TEXT, failure_mode TEXT, node_id TEXT, weak_node_id TEXT, error_text TEXT, gmt_modified INTEGER);`);
});
afterEach(async () => { vi.restoreAllMocks(); await db.close(); });

function issueFixture() {
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, started_at INTEGER);`);
  const now = Date.now();
  raw.prepare("INSERT INTO flow_runs VALUES ('covered', 'wf', ?)").run(now - 10000);
  const add = (id: string, at: number, scope: object, unknowns: string[] = []) => {
    const result = { schemaVersion: 'workflow-evolution-analysis/v1', analysisId: id, facts: [], inferences: [], unknowns,
      diagnoses: [{ diagnosisId: id, flowIds: ['covered'], nodeId: 'node-0', failureSignature: 'issue-0',
        failureMode: 'timeout', severity: 'high', reasoning: 'Observed timeout', evidenceEventIds: [] }] };
    raw.prepare(`INSERT INTO workflow_evolution_analysis_runs (analysis_id, workflow_id, flow_id, scope_type,
      scope_json, status, result_json, requested_at_ms, completed_at_ms) VALUES (?, 'wf', 'covered', 'single_run', ?, 'completed', ?, ?, NULL)`)
      .run(id, JSON.stringify(scope), JSON.stringify(result), at);
  };
  return { now, add, repository: new IssueAggregationRepository(db) };
}

it('counts only observed issue runs despite repeated or incomplete analyses', async () => {
  const { now, add, repository } = issueFixture();
  add('partial', now - 9000, { flowIds: ['covered'], nodeId: 'node-0' }, ['Evidence missing']);
  add('repeat', now - 8000, { flowIds: ['covered'], nodeId: 'node-0' });
  const page = await repository.listPage('wf', { page: 1, pageSize: 20 });
  expect(page.groups[0].priority).toMatchObject({ severity: 'high', day: { affected: 1 }, week: { affected: 1 }, lastOccurredAtMs: now - 10000 });
  expect(page.groups[0].priority?.week).toMatchObject({ related: null, analyzed: null, occurrenceRate: null, sampleHitRate: null });
});

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


it('reads only associated run timestamps regardless of unrelated lifetime and recent runs', async () => {
  const { repository } = await seed(1, 1, 1);
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, started_at INTEGER);
    CREATE TABLE node_executions (flow_id TEXT, node_id TEXT);`);
  const now = Date.now();
  const insert = raw.prepare("INSERT INTO flow_runs VALUES (?, 'wf', ?)");
  raw.transaction(() => {
    for (let i = 0; i < 50001; i++) insert.run(`unrelated-${i}`, now - (i % 2 ? 3600000 : 30 * 86400000));
    insert.run('run-0-0', now - 20 * 86400000);
  })();
  const query = vi.spyOn(db, 'query');
  const page = await repository.listPage('wf', { page: 1, pageSize: 20 });
  expect(page.groups[0].priority).toMatchObject({ day: { affected: 0 }, week: { affected: 0 }, lastOccurredAtMs: now - 20 * 86400000 });
  const runQueries = query.mock.calls.filter(([sql]) => /FROM flow_runs/.test(sql));
  expect(runQueries).toHaveLength(1);
  expect(runQueries[0][0]).toContain('flow_id IN');
  expect(runQueries[0][1]).toEqual(['wf', 'run-0-0']);
  expect(query.mock.calls.some(([sql]) => /node_executions/.test(sql))).toBe(false);
});

it('removes trial evidence from model and legacy issue frequency sources', async () => {
  const { now, add, repository } = issueFixture();
  add('trial-analysis', now - 1000, { flowIds: ['covered'] });
  raw.exec("INSERT INTO workflow_repair_release_records VALUES ('covered', 'trial_run', 'wf')");
  raw.exec("INSERT INTO workflow_healing_diagnoses VALUES ('legacy-trial', 'wf', 'covered', 'trial-only', 'timeout', 'node-0', NULL, 'timeout', 1)");
  expect(await repository.listSources('wf')).toEqual([]);
  const result = await repository.listPage('wf', { page: 1, pageSize: 20 });
  expect(result.groups).toEqual([]);
  expect(result.page.total).toBe(0);
});

it('keeps cold reads independent of unrelated runs and retains versioned history', async () => {
  const {repository}=await seed(1,0,1);
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,workflow_id TEXT,workflow_version INTEGER,workflow_deploy_number INTEGER,state_json TEXT,started_at INTEGER);
    CREATE TABLE workflow_deploy_history(workflow_id TEXT,version INTEGER,deploy_number INTEGER,spec_json TEXT,is_active INTEGER);
    CREATE TABLE node_executions(flow_id TEXT,node_id TEXT);
    INSERT INTO workflow_deploy_history VALUES ('wf',34,34,'{"id":"wf"}',1);`);
  const insert=raw.prepare("INSERT INTO flow_runs VALUES (?,'wf',34,34,NULL,?)");
  const node=raw.prepare("INSERT INTO node_executions VALUES (?,'node-0')");
  const now=Date.now();
  insert.run('run-0-0', now - 1000);
  const query=vi.spyOn(db,'query');
  await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'});
  const baselineQueries = query.mock.calls.length;
  raw.transaction(()=>{for(let i=1;i<35001;i++){const id=`ordinary-${i}`;insert.run(id,now-1000);node.run(id);}})();
  vi.spyOn(Date, 'now').mockReturnValue(now + 11000); // Expire the projection: compare cold reads on both datasets.
  query.mockClear();
  const first=await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'});
  expect(first.groups[0].priority?.week.affected).toBe(1);
  expect(query.mock.calls.some(([sql])=>/node_executions/.test(sql))).toBe(false);
  for (const [sql] of query.mock.calls.filter(([sql])=>/FROM flow_runs/.test(sql))) {
    expect(sql).not.toMatch(/COUNT\(|SUM\(|MAX\(/);
    expect(sql).toMatch(/flow_id IN|WHERE 1 = 0/);
  }
  const runBodies = (await Promise.all(query.mock.calls.flatMap(([sql], i) => /FROM flow_runs WHERE .*flow_id IN/.test(sql) ? [query.mock.results[i].value] : []))).flat();
  expect(runBodies.every(row=>row.flow_id==='run-0-0')).toBe(true);
  console.info('[versioned issue read budget]',{runs:35001,coldQueries:query.mock.calls.length});
  expect(query.mock.calls.length).toBe(baselineQueries);
  expect(query.mock.calls.length).toBeLessThan(20);
  query.mockClear();
  await repository.listPage('wf',{page:2,pageSize:10,versionScope:'current',sort:'recent'});
  expect(query.mock.calls.filter(([sql])=>/SELECT id, analysis_id/.test(sql))).toHaveLength(0);
  expect(query.mock.calls.filter(([sql])=>/FROM node_executions/.test(sql))).toHaveLength(0);
  console.info('[versioned issue read budget]',{warmQueries:query.mock.calls.length});
  raw.prepare("INSERT INTO flow_runs VALUES ('new-run','wf',34,34,NULL,?)").run(now-1000);
  raw.exec("INSERT INTO node_executions VALUES ('new-run','node-0')");
  expect((await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'})).groups[0].priority?.week.affected).toBe(1);
  raw.exec(`UPDATE workflow_deploy_history SET is_active=0; INSERT INTO workflow_deploy_history VALUES ('wf',35,35,'{"id":"wf"}',1);`);
  expect((await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'})).page.total).toBe(0);
  const history=await repository.listPage('wf',{page:1,pageSize:20,versionScope:'historical'});
  expect(history.groups[0].sources[0].analysisContext?.workflowSnapshot).toMatchObject({status:'resolved',snapshot:{workflowVersion:34,deployNumber:34}});
});

it('sorts the full issue set before paging and refreshes only associated run times', async () => {
  const { repository } = await seed(3, 0, 2);
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, started_at INTEGER);`);
  const now = Date.now();
  const insert = raw.prepare("INSERT INTO flow_runs VALUES (?, ?, ?)");
  insert.run('run-0-0', 'wf', Math.floor((now - 2000) / 1000));
  insert.run('run-0-1', 'wf', now - 2000);
  insert.run('run-1-0', 'wf', now - 1000);
  insert.run('run-1-1', 'another-workflow', now - 1000);
  insert.run('run-2-0', 'wf', 0);
  insert.run('run-2-1', 'wf', null);
  const read = (sort: 'priority' | 'frequency' | 'recent', page = 1) => repository.listPage('wf', { page, pageSize: 1, sort });
  expect((await read('priority')).groups[0].signature).toBe('issue-1');
  expect((await read('priority', 2)).groups[0].signature).toBe('issue-0');
  const frequent = (await read('frequency')).groups[0];
  expect(frequent.signature).toBe('issue-0');
  expect(frequent.priority?.week.affected).toBe(2);
  expect((await read('recent')).groups[0].signature).toBe('issue-1');
  expect((await read('recent', 3)).groups[0].priority).toMatchObject({ day: { affected: 0 }, lastOccurredAtMs: null, missingRunTimes: 2 });
  // A late valid time refreshes the lightweight fields even while source projection is cached.
  raw.prepare("UPDATE flow_runs SET started_at=? WHERE flow_id='run-2-0'").run(now - 100);
  expect((await read('recent')).groups[0].signature).toBe('issue-2');
});

it.each(['missing', 'changed'] as const)('preserves a frozen historical analysis snapshot when its run is %s', async state => {
  const { repository } = await seed(1, 0, 1);
  const frozen = { status: 'resolved', snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 34, deployNumber: 34, configDigest: 'a'.repeat(64) } };
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,workflow_id TEXT,workflow_version INTEGER,workflow_deploy_number INTEGER,state_json TEXT,started_at INTEGER);
    CREATE TABLE workflow_deploy_history(workflow_id TEXT,version INTEGER,deploy_number INTEGER,spec_json TEXT,is_active INTEGER);
    INSERT INTO workflow_deploy_history VALUES ('wf',35,35,'{"id":"wf"}',1);`);
  raw.prepare('UPDATE workflow_evolution_analysis_runs SET scope_json=?').run(JSON.stringify({ workflowSnapshot: frozen }));
  if (state === 'changed') raw.prepare("INSERT INTO flow_runs VALUES ('run-0-0','wf',35,35,NULL,?)").run(Date.now());
  expect((await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'})).groups).toEqual([]);
  const history = await repository.listPage('wf',{page:1,pageSize:20,versionScope:'historical'});
  expect(history.groups[0].sources[0].analysisContext?.workflowSnapshot).toEqual(frozen);
});

it.each(['resolved', 'LEGACY_MISSING', 'IDENTITY_MISMATCH', 'wrong-workflow'] as const)('uses the frozen analysis identity with %s binding before run fallback', async kind => {
  const { repository } = await seed(1, 0, 1);
  raw.exec(`ALTER TABLE workflow_evolution_analysis_runs ADD COLUMN state_version INTEGER DEFAULT 0;
    CREATE TABLE flow_runs(flow_id TEXT PRIMARY KEY,workflow_id TEXT,workflow_version INTEGER,workflow_deploy_number INTEGER,state_json TEXT,started_at INTEGER);
    CREATE TABLE workflow_deploy_history(workflow_id TEXT,version INTEGER,deploy_number INTEGER,spec_json TEXT,is_active INTEGER);
    INSERT INTO workflow_deploy_history VALUES ('wf',35,35,'{"id":"wf"}',1);`);
  const frozen = kind === 'resolved' || kind === 'wrong-workflow' ? { status:'resolved', snapshot:{ kind:'deployment', workflowId:kind === 'wrong-workflow'?'other':'wf', workflowVersion:35, deployNumber:35, configDigest:'a'.repeat(64) } } : { status:'unresolved',reason:kind };
  raw.prepare('UPDATE workflow_evolution_analysis_runs SET scope_json=?').run(JSON.stringify({workflowSnapshot:frozen}));
  if(kind !== 'resolved') raw.prepare("INSERT INTO flow_runs VALUES ('run-0-0','wf',35,35,NULL,?)").run(Date.now());
  const current=await repository.listPage('wf',{page:1,pageSize:20,versionScope:'current'});
  expect(current.groups).toHaveLength(kind==='resolved'||kind==='LEGACY_MISSING'?1:0);
  if(kind==='resolved') expect(current.groups[0].sources[0].analysisContext?.workflowSnapshot).toEqual(frozen);
  if(kind==='IDENTITY_MISMATCH'||kind==='wrong-workflow'){
    const history=await repository.listPage('wf',{page:1,pageSize:20,versionScope:'historical'});
    expect(history.groups[0].sources[0].analysisContext?.workflowSnapshot).toEqual({status:'unresolved',reason:'IDENTITY_MISMATCH'});
  }
});
