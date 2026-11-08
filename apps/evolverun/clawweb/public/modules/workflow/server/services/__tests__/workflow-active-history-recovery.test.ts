// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { editorActiveDeploymentToken } from '../editor-active-deployment-token.js';
import { recoverActiveWorkflowHistory, type ActiveHistoryIdentity, type ActiveHistoryRow,
  type AuthorizedReleaseSnapshot } from '../workflow-active-history-recovery.js';

const yaml = `# exact release document\nid: flow\ntitle: Recover\nnodes:\n  - id: a\n    executor: {type: agent, custom: {flag: true}}\ncontent:\n  id: another-workflow\n  nodes: []\n  content: nested user content\ncustom: [one, two]\n`;
const row = (): ActiveHistoryRow => ({ workflow_id: 'flow', is_active: 1, pack_id: 'pack', deploy_number: 9,
  version: 7, tag_name: 'deploy/flow/#9', action: 'deploy',
  spec_json: JSON.stringify({ content: 'id: wrong\nnodes: []', id: 'flow' }) });
const input = (activeRow = row()) => ({ workflowId: 'flow', activeRow,
  expectedActiveToken: editorActiveDeploymentToken('flow', activeRow) });
const release = (identity: ActiveHistoryIdentity): AuthorizedReleaseSnapshot => ({ ...identity,
  source: 'authorized-immutable-release', tagObject: 'a'.repeat(40), releaseCommit: 'b'.repeat(40), yaml });
const trusted = async (identity: ActiveHistoryIdentity) => release(identity);
const unavailable = { kind: 'unavailable' };

describe('active history recovery from an authenticated host release reader', () => {
  it('keeps exact YAML and preserves all user fields without interpreting content as an envelope', async () => {
    const result = await recoverActiveWorkflowHistory(input(), trusted);
    expect(result.kind).toBe('recovered');
    if (result.kind !== 'recovered') throw new Error('expected recovery');
    expect(result.yaml).toBe(yaml);
    expect(result.activeToken).toBe(input().expectedActiveToken);
    expect(JSON.parse(result.specJson)).toEqual({ id: 'flow', title: 'Recover', nodes: [
      { id: 'a', executor: { type: 'agent', custom: { flag: true } } },
    ], content: { id: 'another-workflow', nodes: [], content: 'nested user content' }, custom: ['one', 'two'] });
  });

  it('keeps a YAML-looking string content as user data, even when the historical row contains an archive', async () => {
    const archive = 'id: nested\nnodes: []\ncontent: still user data\n';
    const document = `id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: ${JSON.stringify(archive)}\n`;
    const active = { ...row(), spec_json: JSON.stringify({ content: document }) };
    const result = await recoverActiveWorkflowHistory(input(active), async id => ({ ...release(id), yaml: document }));
    expect(result.kind).toBe('recovered');
    if (result.kind !== 'recovered') throw new Error('expected recovery');
    expect(result.yaml).toBe(document);
    expect(JSON.parse(result.specJson)).toEqual({ id: 'flow', nodes: [{ id: 'a', executor: { type: 'agent' } }], content: archive });
  });

  it('supports exact string numbers from a DB driver and freezes the server identity', async () => {
    const active = { ...row(), version: '7', deploy_number: '9' };
    const reader = vi.fn(async (identity: ActiveHistoryIdentity) => {
      expect(Object.isFrozen(identity)).toBe(true);
      expect(identity).toEqual({ workflowId: 'flow', packId: 'pack', deployNumber: '9', version: '7',
        tagName: 'deploy/flow/#9', activeToken: input(active).expectedActiveToken });
      return release(identity);
    });
    expect((await recoverActiveWorkflowHistory(input(active), reader)).kind).toBe('recovered');
    expect(reader).toHaveBeenCalledOnce();
  });

  it.each([
    { workflow_id: 'other' }, { is_active: 0 }, { action: 'rollback' }, { action: 'edit' },
    { pack_id: '../pack' }, { tag_name: null }, { tag_name: 'deploy/other/#9' },
    { tag_name: 'deploy/flow/#8' }, { deploy_number: 0 }, { version: 0 },
    { version: '07' }, { deploy_number: Number.MAX_SAFE_INTEGER + 1 },
  ])('rejects invalid or unsupported active row %o before accessing the reader', async change => {
    const active = { ...row(), ...change };
    const reader = vi.fn(trusted);
    // Some malformed numeric forms are rejected by token creation itself.
    let expected: string;
    try { expected = editorActiveDeploymentToken('flow', active); } catch { expected = 'a'.repeat(64); }
    expect(await recoverActiveWorkflowHistory({ workflowId: 'flow', activeRow: active, expectedActiveToken: expected }, reader)).toEqual(unavailable);
    expect(reader).not.toHaveBeenCalled();
  });

  it('does not call the reader if the token is stale or malformed', async () => {
    const reader = vi.fn(trusted);
    for (const token of ['a'.repeat(64), '', 'ABC']) {
      expect(await recoverActiveWorkflowHistory({ ...input(), expectedActiveToken: token }, reader)).toEqual(unavailable);
    }
    expect(reader).not.toHaveBeenCalled();
  });

  it.each([
    { workflowId: 'other' }, { packId: 'other' }, { deployNumber: '8' }, { version: '8' },
    { tagName: 'deploy/other/#9' }, { activeToken: 'a'.repeat(64) }, { source: 'unknown' },
    { tagObject: 'bad' }, { releaseCommit: 'bad' },
  ])('rejects a mismatched or unverified release binding %o', async change => {
    expect(await recoverActiveWorkflowHistory(input(), async id => ({ ...release(id), ...change }) as AuthorizedReleaseSnapshot))
      .toEqual(unavailable);
  });

  it('fails closed for absent provenance and does not leak reader errors', async () => {
    expect(await recoverActiveWorkflowHistory(input(), async () => null)).toEqual(unavailable);
    expect(await recoverActiveWorkflowHistory(input(), async () => { throw new Error('secret remote credential'); }))
      .toEqual(unavailable);
  });

  it.each([
    'id: other\nnodes: [{id: a, executor: {type: agent}}]\n',
    'id: flow\nid: flow\nnodes: [{id: a, executor: {type: agent}}]\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: &x {a: b}\nother: *x\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: !special value\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: 9007199254740993\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: 0.10000000000000001\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\n---\nid: other\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: "\uFFFD"\n',
    'id: flow\nnodes: [{id: a, executor: {type: agent}}]\ncontent: ' + 'x'.repeat(1024 * 1024),
  ])('rejects YAML that cannot be safely projected', async badYaml => {
    expect(await recoverActiveWorkflowHistory(input(), async id => ({ ...release(id), yaml: badYaml }))).toEqual(unavailable);
  });

  it('snapshots the active row before awaiting, but is not a transactional recheck', async () => {
    const active = row();
    const request = input(active);
    let finish!: (value: AuthorizedReleaseSnapshot) => void;
    let captured!: ActiveHistoryIdentity;
    const pending = recoverActiveWorkflowHistory(request, id => { captured = id; return new Promise(resolve => { finish = resolve; }); });
    Object.assign(active, { pack_id: 'changed', is_active: 0 });
    finish(release(captured));
    expect((await pending).kind).toBe('recovered');
  });
});
