import { describe, expect, it, vi } from 'vitest';
import { FanoutReadError, type FanoutReadErrorCode, type RunReadInspection, type RunSubworkflowsReader } from '../../contracts/run-subworkflows.js';
import { readVisibleRunLineage } from '../run-launch-lineage.js';

const actor = { actorId: 'viewer', isAdmin: false };
function inspection(fields: Record<string, unknown> = {}, flags: Partial<RunReadInspection> = {}): RunReadInspection {
  return { run: { flow_id: 'child', workflow_id: 'wf', origin_bot_id: 'bot:owner', status: 'running',
    workflow_title: null, started_at: 1, completed_at: null, total_duration_ms: null, state_json: '{}',
    origin_session_key: null, origin_session_id: null, ...fields },
    managed: false, restricted: false, groups: [], ...flags };
}
function fixture() {
  const inspectRun = vi.fn(async (_actor, id: string) => inspection({ flow_id: id }));
  const reader = { inspectRun } as unknown as RunSubworkflowsReader;
  return { reader, inspectRun };
}

describe('authorized ordinary-detail lineage', () => {
  it('independently inspects exact stored IDs with the same actor and returns only lineage fields', async () => {
    const { reader, inspectRun } = fixture();
    const fields = { parent_flow_id: ' parent /?根 ', root_flow_id: 'ROOT#%e\u0301',
      params_json: '{"parent_flow_id":"forged"}', input_json: '{"root_flow_id":"forged"}' };
    inspectRun.mockImplementation(async (_actor, id) => inspection({ flow_id: id, credentials_json: 'never-copy', parent_flow_id: 'never-traverse' }));
    expect(await readVisibleRunLineage(reader, actor, inspection(fields))).toEqual({
      parent_flow_id: fields.parent_flow_id, root_flow_id: fields.root_flow_id,
    });
    expect(inspectRun.mock.calls).toEqual([[actor, fields.parent_flow_id], [actor, fields.root_flow_id]]);
  });

  it.each([true, false])('deduplicates identical parent/root lookups (visible: %s)', async allowed => {
    const { reader, inspectRun } = fixture();
    if (!allowed) inspectRun.mockRejectedValue(new FanoutReadError('forbidden', 'denied'));
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent', root_flow_id: 'parent' })))
      .toEqual(allowed ? { parent_flow_id: 'parent', root_flow_id: 'parent' } : {});
    expect(inspectRun).toHaveBeenCalledExactlyOnceWith(actor, 'parent');
  });

  it.each(['forbidden', 'not_found'] as const)('omits only the %s ancestor and still authorizes the other', async code => {
    const { reader, inspectRun } = fixture();
    inspectRun.mockRejectedValueOnce(new FanoutReadError(code, 'hidden'));
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent', root_flow_id: 'root' })))
      .toEqual({ root_flow_id: 'root' });
    expect(inspectRun.mock.calls).toEqual([[actor, 'parent'], [actor, 'root']]);
  });

  it('does not trust case-insensitive or otherwise mismatched lookup results', async () => {
    const { reader, inspectRun } = fixture();
    inspectRun.mockResolvedValue(inspection({ flow_id: 'PARENT' }));
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent' }))).toEqual({});
  });

  it.each([{ managed: true }, { restricted: true }, { sensitiveChild: true }])('withholds all lineage for %j', async flags => {
    const { reader, inspectRun } = fixture();
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent', root_flow_id: null }, flags))).toEqual({});
    expect(inspectRun).not.toHaveBeenCalled();
  });

  it('preserves explicit nulls without reads and never infers missing fields from JSON', async () => {
    const { reader, inspectRun } = fixture();
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: null, root_flow_id: null })))
      .toEqual({ parent_flow_id: null, root_flow_id: null });
    expect(await readVisibleRunLineage(reader, actor, inspection({ params_json: '{"parent_flow_id":"parent"}',
      input_json: '{"root_flow_id":"root"}', state_json: '{"subworkflowMeta":{"parentFlowId":"parent"}}' }))).toEqual({});
    const inherited = inspection();
    Object.setPrototypeOf(inherited.run, { parent_flow_id: 'parent', root_flow_id: 'root' });
    expect(await readVisibleRunLineage(reader, actor, inherited)).toEqual({});
    expect(inspectRun).not.toHaveBeenCalled();
  });

  it.each([undefined, '', ' \t\n', 17, false, [], {}, 'x'.repeat(256), '\uD800', '\uDC00'])('omits invalid stored ID %j without reading it', async id => {
    const { reader, inspectRun } = fixture();
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: id, root_flow_id: null })))
      .toEqual({ root_flow_id: null });
    expect(inspectRun).not.toHaveBeenCalled();
  });

  it('accepts 255 Unicode codepoints unchanged', async () => {
    const { reader } = fixture();
    const id = '🚀'.repeat(255);
    expect(await readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: id }))).toEqual({ parent_flow_id: id });
  });

  const propagated: FanoutReadErrorCode[] = ['unauthenticated', 'unavailable', 'invalid_state', 'invalid_query', 'identity_conflict', 'snapshot_changed'];
  it.each(propagated)('propagates %s rather than hiding a failed authorization/read', async code => {
    const { reader, inspectRun } = fixture();
    const error = new FanoutReadError(code, 'failed');
    inspectRun.mockRejectedValue(error);
    await expect(readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent' }))).rejects.toBe(error);
  });

  it.each([new Error('storage failed'), { code: 'forbidden' }, new SyntaxError('invalid state')])('propagates non-domain errors unchanged: %j', async error => {
    const { reader, inspectRun } = fixture();
    inspectRun.mockRejectedValue(error);
    await expect(readVisibleRunLineage(reader, actor, inspection({ parent_flow_id: 'parent' }))).rejects.toBe(error);
  });
});
