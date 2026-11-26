import { afterEach, describe, expect, it, vi } from 'vitest';
import { parse } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { workflowConfigurationDigest } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { ExecutionRecordWorkflowRepository, isMissingExecutionRecordWorkflowAssociation } from '../execution-record-workflow-repository.js';
import { FlowRunRepository } from '../flow-run-repository.js';
import { commit, executedSpec, executionRecordWorkflowFixture, workflowRun } from './execution-record-workflow.fixture.js';

const opened: IDatabase[] = [];
afterEach(async () => { vi.restoreAllMocks(); for (const db of opened.splice(0)) await db.close(); });
function fixture(association = true) {
  const value = executionRecordWorkflowFixture(association); opened.push(value.db); return value;
}
const missing = (reason: string) => ({ workflow_yaml: null, workflow_snapshot: { status: 'unresolved', reason } });
const deployedRef = { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 10,
  configDigest: workflowConfigurationDigest(executedSpec) };
const stored = (snapshot: unknown) => JSON.stringify({ status: 'resolved', snapshot });
const savedState = (overrides: Record<string, unknown> = {}) => JSON.stringify({ flowId: 'flow', workflowId: 'wf',
  workflowPin: { source: 'remote-snapshot', workflowId: 'wf', workflowDigest: `remote-snapshot:${commit}`, packDigest: 'pack-digest' },
  workflowSnapshot: executedSpec, ...overrides });

// These tests exercise real SQLite and the public repository delegation, not the unsafe shared run reader.
describe('execution record executed workflow', () => {
  it('returns v1 while mutable latest and active v2 differ, with exact bounded SQL only', async () => {
    const { db, raw, repository, deployment } = fixture(); deployment();
    const latest = { ...executedSpec, title: 'Latest v2', nodes: [] };
    deployment(latest, 2, 11, 1);
    raw.prepare('INSERT INTO workflow_specs VALUES (?,?)').run('wf', JSON.stringify(latest));
    const query = vi.spyOn(db, 'query');
    const run = await repository.findExecutionRecordByFlowIdStrict('flow');
    const result = await repository.readExecutionRecordWorkflow(run!);
    expect(parse(result.workflow_yaml!)).toEqual(executedSpec);
    expect(result.workflow_snapshot).toEqual({ status: 'resolved', snapshot: deployedRef });
    expect(query.mock.calls).toHaveLength(2);
    expect(query.mock.calls[1]).toEqual([
      'SELECT workflow_id, version, deploy_number, spec_json FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?', ['wf', 10],
    ]);
    const sql = query.mock.calls.map(([text]) => text).join('\n').replace(/NULL AS credentials_json/g, '');
    expect(sql).not.toMatch(/SELECT\s+\*|credentials_json|workflow_specs|is_active|ORDER BY|LIMIT|engine/i);
    expect(sql).toMatch(/workflow_version, workflow_deploy_number, workflow_snapshot_json/);
  });

  it('selects the exact deployment even for rollback/repeated versions', async () => {
    const { db, repository, deployment } = fixture(); deployment();
    deployment({ ...executedSpec, title: 'Same version, different deployment' }, 1, 12, 1);
    const read = new ExecutionRecordWorkflowRepository(db);
    expect(parse((await read.read(workflowRun())).workflow_yaml!)).toEqual(executedSpec);
    expect(parse((await read.read(workflowRun({ workflow_deploy_number: 12 }))).workflow_yaml!).title).toBe('Same version, different deployment');
    expect((await repository.readExecutionRecordWorkflow(workflowRun())).workflow_snapshot).toMatchObject({ snapshot: { deployNumber: 10 } });
  });

  it('preserves older raw YAML wrapper bytes/comments and hashes the stored object, not expanded YAML', async () => {
    const { repository, deployment } = fixture();
    const yaml = '# original comment\r\nid: wf\r\nnodes: []\r\nbusiness:\r\n  enabled: false\r\n';
    const wrapper = { content: yaml }; deployment(wrapper);
    const result = await repository.readExecutionRecordWorkflow(workflowRun());
    expect(result.workflow_yaml).toBe(yaml);
    expect(result.workflow_snapshot).toMatchObject({ snapshot: { configDigest: workflowConfigurationDigest(wrapper) } });
    expect(workflowConfigurationDigest(wrapper)).not.toBe(workflowConfigurationDigest(parse(yaml)));
  });

  it('keeps structured business keys and content when nodes exist; YAML is semantically equivalent', async () => {
    const { repository, deployment } = fixture();
    const spec = { ...executedSpec, content: 'a business field, not replacement YAML', 'dot.key': { '__key': 'null' },
      multiline: 'first\nsecond', long: 'business-value '.repeat(100) };
    deployment(spec);
    const result = await repository.readExecutionRecordWorkflow(workflowRun());
    expect(parse(result.workflow_yaml!)).toEqual(spec);
    expect(result.workflow_snapshot).toMatchObject({ snapshot: { configDigest: workflowConfigurationDigest(spec) } });
  });

  it('reads only the captured saved snapshot and its pin, without any database/Git/draft reads', async () => {
    const { db, repository, raw } = fixture();
    raw.prepare('INSERT INTO workflow_specs VALUES (?,?)').run('wf', JSON.stringify({ ...executedSpec, title: 'Mutable draft' }));
    const query = vi.spyOn(db, 'query');
    const run = workflowRun({ workflow_version: -1, workflow_deploy_number: null, state_json: savedState() });
    const result = await repository.readExecutionRecordWorkflow(run);
    expect(parse(result.workflow_yaml!)).toEqual(executedSpec);
    expect(result.workflow_snapshot).toEqual({ status: 'resolved', snapshot: {
      kind: 'saved', workflowId: 'wf', commit, sourceFlowId: 'flow', packDigest: 'pack-digest',
      configDigest: workflowConfigurationDigest(executedSpec),
    } });
    expect(query).not.toHaveBeenCalled();
  });

  it.each([
    { workflow_version: null }, { workflow_deploy_number: null }, { workflow_version: null, workflow_deploy_number: null },
  ])('does not invent history for legacy versions: %j', async overrides => {
    const { db, repository, deployment } = fixture(); deployment();
    const query = vi.spyOn(db, 'query');
    expect(await repository.readExecutionRecordWorkflow(workflowRun(overrides))).toEqual(missing('LEGACY_MISSING'));
    expect(query).not.toHaveBeenCalled();
  });

  it.each([0, -2, 1.5, Number.MAX_SAFE_INTEGER + 1, '1'])('rejects a nonpositive/unsafe/untyped version %s', async version => {
    const { repository } = fixture();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_version: version as number }))).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, '10'])('rejects an invalid deployment number %s', async number => {
    const { repository } = fixture();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_deploy_number: number as number }))).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it('returns snapshot missing instead of latest fallback for missing exact history', async () => {
    const { repository, deployment } = fixture(); deployment(executedSpec, 2, 11, 1);
    expect(await repository.readExecutionRecordWorkflow(workflowRun())).toEqual(missing('SNAPSHOT_MISSING'));
  });

  it.each(['duplicate', 'version', 'id', 'deploy'])('rejects ambiguous or mismatched history rows: %s', async kind => {
    const { db, repository, deployment } = fixture(); deployment();
    if (kind === 'duplicate') deployment();
    else vi.spyOn(db, 'query').mockResolvedValue([{ workflow_id: kind === 'id' ? 'other' : 'wf',
      version: kind === 'version' ? 2 : 1, deploy_number: kind === 'deploy' ? 11 : 10, spec_json: JSON.stringify(executedSpec) }]);
    expect(await repository.readExecutionRecordWorkflow(workflowRun())).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each(['broken {', 'null', '[]', '"scalar"', '{"id":"wf","nodes":null}',
    '{"id":"other","id":"wf","nodes":[]}', '{"id":"wf","nodes":[],"business":{"key":1,"key":2}}',
    '{"id":"wf","nodes":[],"large":9007199254740993}', '{"id":"wf","nodes":[],"infinity":1e999}'])
  ('reports malformed/unsupported/ambiguous stored JSON as unavailable: %s', async text => {
    const { repository, deployment } = fixture(); deployment(text);
    expect(await repository.readExecutionRecordWorkflow(workflowRun())).toEqual(missing('SNAPSHOT_MISSING'));
  });

  it.each(['id: wf\nid: wf\nnodes: []\n', 'id: wf\nnodes: []\n---\nid: wf\nnodes: []\n',
    'id: wf\nnodes: [', '[wf]', 'id: wf\nnodes: []\nx: &x value\ny: *x\n',
    'id: wf\nnodes: []\nx: !!str value\n', 'id: wf\nnodes: []\n? [key]\n: value\n'])
  ('rejects duplicate/multidocument/unsupported YAML wrappers: %s', async content => {
    const { repository, deployment } = fixture(); deployment({ content });
    expect(await repository.readExecutionRecordWorkflow(workflowRun())).toEqual(missing('SNAPSHOT_MISSING'));
  });

  it.each([{ id: 'other', nodes: [] }, { content: 'id: other\nnodes: []\n' }, { id: 'other', content: 'id: wf\nnodes: []\n' }])
  ('reports stored spec identity mismatch: %j', async spec => {
    const { repository, deployment } = fixture(); deployment(spec);
    expect(await repository.readExecutionRecordWorkflow(workflowRun())).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each([null, '{}', JSON.stringify({ workflowSnapshot: executedSpec }), JSON.stringify({ workflowPin: {} })])
  ('returns legacy missing for a saved run without captured provenance: %s', async state_json => {
    const { repository } = fixture();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_version: -1, state_json }))).toEqual(missing('LEGACY_MISSING'));
  });

  it.each([
    { workflowPin: { source: 'other', workflowId: 'wf', workflowDigest: `remote-snapshot:${commit}` } },
    { workflowPin: { source: 'remote-snapshot', workflowId: 'other', workflowDigest: `remote-snapshot:${commit}` } },
    { workflowPin: { source: 'remote-snapshot', workflowId: 'wf', workflowDigest: 'remote-snapshot:invalid' } },
    { workflowSnapshot: { id: 'other', nodes: [] } }, { flowId: 'other' }, { workflowId: 'other' },
  ])('rejects mismatched saved pin/spec/state identity: %j', async state => {
    const { repository } = fixture();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_version: -1, state_json: savedState(state) }))).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it('honors a stored resolved deployment pin and never returns changed content under its digest', async () => {
    const { repository, deployment, raw } = fixture(); deployment();
    const run = workflowRun({ workflow_snapshot_json: stored(deployedRef) });
    expect((await repository.readExecutionRecordWorkflow(run)).workflow_snapshot).toEqual({ status: 'resolved', snapshot: deployedRef });
    raw.prepare('UPDATE workflow_deploy_history SET spec_json = ?').run(JSON.stringify({ ...executedSpec, title: 'Tampered' }));
    expect(await repository.readExecutionRecordWorkflow(run)).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each([{ workflowId: 'other' }, { workflowVersion: 2 }, { deployNumber: 11 }, { configDigest: 'b'.repeat(64) }])
  ('rejects a stored deployment association mismatch: %j', async change => {
    const { repository, deployment } = fixture(); deployment();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_snapshot_json: stored({ ...deployedRef, ...change }) })))
      .toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each([{ commit: 'b'.repeat(40) }, { sourceFlowId: 'other' }, { workflowId: 'other' }, { configDigest: 'c'.repeat(64) }, { packDigest: 'other' }])
  ('rejects stored saved provenance mismatch: %j', async change => {
    const { repository } = fixture();
    const ref = { kind: 'saved', workflowId: 'wf', commit, sourceFlowId: 'flow', configDigest: workflowConfigurationDigest(executedSpec),
      packDigest: 'pack-digest', ...change };
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_version: -1, state_json: savedState(), workflow_snapshot_json: stored(ref) })))
      .toEqual(missing('IDENTITY_MISMATCH'));
  });

  it.each(['{broken', '{}', '{"status":"resolved","status":"unresolved","reason":"LEGACY_MISSING"}'])
  ('rejects malformed/ambiguous stored association rather than ignoring it: %s', async workflow_snapshot_json => {
    const { repository, deployment } = fixture(); deployment();
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_snapshot_json }))).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it('does not expose extra opaque stored association fields', async () => {
    const { repository, deployment } = fixture(); deployment();
    const result = await repository.readExecutionRecordWorkflow(workflowRun({ workflow_snapshot_json: stored({ ...deployedRef, credentials: 'synthetic-should-not-leak' }) }));
    expect(result.workflow_snapshot).toEqual({ status: 'resolved', snapshot: deployedRef });
  });

  it('bounds the saved full state at 8 MiB before parsing while accepting the inclusive boundary', async () => {
    const { repository } = fixture();
    const state = JSON.parse(savedState());
    state.unrelated = '';
    const overhead = Buffer.byteLength(JSON.stringify(state));
    state.unrelated = 'x'.repeat(8 * 1024 * 1024 - overhead);
    const state_json = JSON.stringify(state);
    expect(Buffer.byteLength(state_json)).toBe(8 * 1024 * 1024);
    const run = workflowRun({ workflow_version: -1, state_json });
    expect((await repository.readExecutionRecordWorkflow(run)).workflow_snapshot.status).toBe('resolved');
    expect(await repository.readExecutionRecordWorkflow({ ...run, state_json: state_json + ' ' })).toEqual(missing('SNAPSHOT_MISSING'));
  });

  it('bounds a stored association at 64 KiB before parsing while accepting the inclusive boundary', async () => {
    const { repository, deployment } = fixture(); deployment();
    const binding = { status: 'resolved', snapshot: deployedRef, unrelated: '' };
    const overhead = Buffer.byteLength(JSON.stringify(binding));
    binding.unrelated = 'x'.repeat(64 * 1024 - overhead);
    const workflow_snapshot_json = JSON.stringify(binding);
    expect(Buffer.byteLength(workflow_snapshot_json)).toBe(64 * 1024);
    expect((await repository.readExecutionRecordWorkflow(workflowRun({ workflow_snapshot_json }))).workflow_snapshot.status).toBe('resolved');
    expect(await repository.readExecutionRecordWorkflow(workflowRun({ workflow_snapshot_json: workflow_snapshot_json + ' ' }))).toEqual(missing('IDENTITY_MISMATCH'));
  });

  it('propagates history infrastructure failure instead of reporting unavailable history', async () => {
    const { db, repository } = fixture();
    const failure = new Error('synthetic SQL details'); vi.spyOn(db, 'query').mockRejectedValue(failure);
    await expect(repository.readExecutionRecordWorkflow(workflowRun())).rejects.toBe(failure);
  });
});

describe('strict same-row workflow provenance selection', () => {
  it('captures the association, version/deploy/state together and does not reread a changed run', async () => {
    const { raw, db, repository, deployment } = fixture(); deployment();
    raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(stored(deployedRef));
    const selected = await repository.findExecutionRecordByFlowIdStrict('flow');
    expect(selected).toMatchObject({ workflow_version: 1, workflow_deploy_number: 10, workflow_snapshot_json: stored(deployedRef) });
    raw.prepare('UPDATE flow_runs SET workflow_version = 2, workflow_deploy_number = 11, state_json = ?').run('opaque changed state');
    const query = vi.spyOn(db, 'query');
    expect((await repository.readExecutionRecordWorkflow(selected!)).workflow_snapshot).toEqual({ status: 'resolved', snapshot: deployedRef });
    expect(query.mock.calls.every(([sql]) => !/FROM flow_runs/i.test(sql))).toBe(true);
  });

  it('supports real legacy schemas only via a credential-free NULL association fallback', async () => {
    const { db, repository, deployment } = fixture(false); deployment();
    const query = vi.spyOn(db, 'query');
    const run = await repository.findExecutionRecordByFlowIdStrict('flow');
    expect(run!.workflow_snapshot_json).toBeNull();
    expect(query).toHaveBeenCalledTimes(2);
    expect(query.mock.calls[1][0]).toMatch(/NULL AS workflow_snapshot_json/);
    expect(query.mock.calls.every(([sql]) => !/SELECT\s+\*|credentials_json/i.test(sql.replace(/NULL AS credentials_json/i, '')))).toBe(true);
    expect((await repository.readExecutionRecordWorkflow(run!)).workflow_snapshot.status).toBe('resolved');
  });

  it.each(['Unknown column \'workflow_snapshot_json\' in \'field list\'', "ER_BAD_FIELD_ERROR: Unknown column: 'workflow_snapshot_json' in 'field list'", 'no such column: workflow_snapshot_json'])
  ('recognizes only known missing association-column errors: %s', async message => {
    const selected = workflowRun(); const query = vi.fn().mockRejectedValueOnce(new Error(message)).mockResolvedValueOnce([selected]);
    expect(isMissingExecutionRecordWorkflowAssociation(new Error(message))).toBe(true);
    expect(await new FlowRunRepository({ query } as unknown as IDatabase).findExecutionRecordByFlowIdStrict('flow')).toEqual(selected);
    expect(query).toHaveBeenCalledTimes(2);
  });

  it.each(['database unavailable', 'permission denied: workflow_snapshot_json', 'no such column: state_json',
    'no such column: workflow_snapshot_json_backup', 'no such table: flow_runs'])('does not swallow other schema/infrastructure errors: %s', async message => {
    const failure = new Error(message); const query = vi.fn().mockRejectedValue(failure);
    await expect(new FlowRunRepository({ query } as unknown as IDatabase).findExecutionRecordByFlowIdStrict('flow')).rejects.toBe(failure);
    expect(query).toHaveBeenCalledTimes(1);
  });
});
