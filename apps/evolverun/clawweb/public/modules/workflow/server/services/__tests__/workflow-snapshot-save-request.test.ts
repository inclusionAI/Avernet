// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { fingerprintWorkflowSnapshotSaveRequest, WORKFLOW_SNAPSHOT_SAVE_REQUEST_VERSION } from '../workflow-snapshot-save-request.js';

const oid = 'a'.repeat(40);
const request = () => ({
  requestId: 'request_123', actorId: 'actor-1', workflowId: 'flow', packId: 'pack',
  repositoryId: 'c'.repeat(64), sourceVersion: 'current' as const, sourceCommit: oid,
  expectedSavedHead: null as string | null, expectedDraftToken: 'd'.repeat(64),
  specJson: '{"id":"flow","nodes":[{"id":"a","executor":{"type":"agent"}}],"count":1}',
  changes: [
    { path: 'scripts/a.sh', operation: 'create' as const, contentBase64: 'YQ==', mode: '100644' as const },
    { path: 'scripts/b.sh', operation: 'delete' as const },
  ],
});
const hash = (v: unknown) => fingerprintWorkflowSnapshotSaveRequest(v as ReturnType<typeof request>).inputHash;

describe('versioned workflow snapshot save request', () => {
  it('returns a stable fingerprint, operation and lossless projection', () => {
    const input = request(); const result = fingerprintWorkflowSnapshotSaveRequest(input);
    expect(result.version).toBe(WORKFLOW_SNAPSHOT_SAVE_REQUEST_VERSION);
    expect(result.inputHash).toBe('3bf8b5921c4826c0c3b3c227c48083bce992c287e07e2a6a08a6406bdd5bf78e');
    expect(result.request.expectedDraftToken).toBe(input.expectedDraftToken);
    expect(result.inputHash).toBe(hash(request()));
    expect(JSON.parse(result.projection.specJson)).toEqual(JSON.parse(input.specJson));
    expect(result.operation).toEqual({ requestId: input.requestId, workflowId: 'flow', actorId: 'actor-1', kind: 'save', inputHash: result.inputHash });
  });
  it('allows retry with the same ID and scopes a different ID without changing content hash', () => {
    const a = fingerprintWorkflowSnapshotSaveRequest(request());
    const b = fingerprintWorkflowSnapshotSaveRequest({ ...request(), requestId: 'request_456' });
    expect(a.inputHash).toBe(b.inputHash);
    expect(a.operation.requestId).not.toBe(b.operation.requestId);
    expect(fingerprintWorkflowSnapshotSaveRequest(request())).toEqual(a);
  });
  it('binds every semantic field, each change field and order', () => {
    const a = request(); const digest = hash(a);
    const mutations: unknown[] = [
      { ...a, actorId: 'actor-2' }, { ...a, workflowId: 'other', specJson: a.specJson.replace('"flow"', '"other"') },
      { ...a, packId: 'other' }, { ...a, repositoryId: 'd'.repeat(64) },
      { ...a, sourceVersion: 'draft', expectedSavedHead: oid }, { ...a, sourceCommit: 'b'.repeat(40) },
      { ...a, expectedSavedHead: oid }, { ...a, expectedDraftToken: 'e'.repeat(64) }, { ...a, specJson: a.specJson.replace('"count":1', '"count":2') },
      { ...a, changes: [...a.changes].reverse() }, { ...a, changes: a.changes.slice(0, 1) },
      { ...a, changes: [{ ...a.changes[0], path: 'scripts/c.sh' }, a.changes[1]] },
      { ...a, changes: [{ ...a.changes[0], operation: 'replace' }, a.changes[1]] },
      { ...a, changes: [{ ...a.changes[0], contentBase64: 'Yg==' }, a.changes[1]] },
      { ...a, changes: [{ ...a.changes[0], mode: '100755' }, a.changes[1]] },
    ];
    for (const mutation of mutations) expect(hash(mutation)).not.toBe(digest);
  });
  it('does not normalize raw JSON spelling, whitespace, object order or Unicode', () => {
    const a = request();
    for (const specJson of [a.specJson.replace('"count":1', '"count":1.0'), ` ${a.specJson}`,
      a.specJson.replace('"id":"flow","nodes":', '"nodes":').replace('],"count":1}', '],"count":1,"id":"flow"}'),
      a.specJson.replace('"flow"', '"\\u0066low"')]) {
      const b = fingerprintWorkflowSnapshotSaveRequest({ ...a, specJson });
      expect(b.projection.specJson).toBe(fingerprintWorkflowSnapshotSaveRequest(a).projection.specJson);
      expect(b.inputHash).not.toBe(hash(a));
    }
  });
  it.each(['9007199254740993', '0.10000000000000001', '-0'])('rejects lossy number %s before parse', number => {
    expect(() => hash({ ...request(), specJson: request().specJson.replace('"count":1', `"count":${number}`) })).toThrow();
  });
  it('rejects duplicate keys and mismatched or invalid workflow documents', () => {
    for (const specJson of [request().specJson.replace('"id":"flow"', '"id":"flow","id":"flow"'), '{"id":"other","nodes":[]}', '{"id":"flow"}', 'not json', `{"id":"flow","nodes":[],"x":"\ud800"}`]) {
      expect(() => hash({ ...request(), specJson })).toThrow();
    }
  });
  it('rejects unknown, missing, unsafe and credential-bearing envelope fields', () => {
    const a = request();
    for (const input of [null, [], { ...a, token: 'secret' }, { ...a, remoteUrl: 'https://user:pass@example.com/repo' },
      { ...a, repositoryId: 'https://user:pass@example.com/repo' }, { ...a, repositoryId: 'A'.repeat(64) },
      { ...a, actorId: ' bad' }, { ...a, actorId: 'https://x' }, { ...a, workflowId: '../flow' },
      { ...a, packId: 'p.lock' }, { ...a, requestId: '' }, { ...a, sourceCommit: 'A'.repeat(40) },
      { ...a, sourceVersion: 'initial' }, { ...a, expectedSavedHead: undefined },
      { ...a, expectedDraftToken: undefined }, { ...a, expectedDraftToken: null },
      { ...a, expectedDraftToken: 'D'.repeat(64) }, { ...a, expectedDraftToken: 'e'.repeat(63) }, { ...a, specJson: JSON.parse(a.specJson) },
      { ...a, changes: [{ ...a.changes[0], token: 'secret' }] }, { ...a, changes: [{ ...a.changes[1], mode: '100644' }] },
    ]) expect(() => hash(input)).toThrow();
  });
  it('rejects unsafe paths, duplicates, noncanonical base64 and unsupported modes', () => {
    const a = request();
    for (const path of ['', '../x', '/x', 'a//b', 'a/./b', 'a/.git/config', 'a/\\b', 'a/\u0000b', 'C:/x', 'a/\u0085', 'a/\ud800']) {
      expect(() => hash({ ...a, changes: [{ ...a.changes[0], path }] })).toThrow();
    }
    for (const contentBase64 of ['YR==', 'YQ', 'YQ==\n', '****']) expect(() => hash({ ...a, changes: [{ ...a.changes[0], contentBase64 }] })).toThrow();
    expect(() => hash({ ...a, changes: [{ ...a.changes[0], mode: '120000' }] })).toThrow();
    expect(() => hash({ ...a, changes: [a.changes[0], a.changes[0]] })).toThrow();
  });
  it('rejects accessors, hidden or symbol keys, sparse arrays and exotic prototypes without executing getters', () => {
    const a = request(); let invoked = false;
    const getter = { ...a }; Object.defineProperty(getter, 'actorId', { enumerable: true, get() { invoked = true; return 'actor'; } });
    const hidden = { ...a }; Object.defineProperty(hidden, 'secret', { value: 'x' });
    const symbol = { ...a, [Symbol('secret')]: 'x' };
    const sparse = new Array(1);
    const extra = [a.changes[0]]; Object.assign(extra, { secret: 'x' });
    for (const input of [getter, hidden, symbol, Object.assign(Object.create({}), a), { ...a, changes: sparse }, { ...a, changes: extra }]) expect(() => hash(input)).toThrow();
    expect(invoked).toBe(false);
  });
  it('changes the hash for a different draft token even when requestId is reused', () => {
    const a = fingerprintWorkflowSnapshotSaveRequest(request());
    const b = fingerprintWorkflowSnapshotSaveRequest({ ...request(), expectedDraftToken: 'e'.repeat(64) });
    expect(b.operation.requestId).toBe(a.operation.requestId);
    expect(b.inputHash).not.toBe(a.inputHash);
  });
  it('bounds file changes and accepts canonical empty content', () => {
    const a = request();
    expect(() => hash({ ...a, changes: [{ ...a.changes[0], contentBase64: '' }] })).not.toThrow();
    expect(() => hash({ ...a, changes: Array.from({ length: 501 }, (_, i) => ({ path: `p${i}`, operation: 'delete' })) })).toThrow();
    expect(() => hash({ ...a, changes: [{ ...a.changes[0], contentBase64: Buffer.alloc(1024 * 1024 + 1).toString('base64') }] })).toThrow();
    expect(() => hash({ ...a, changes: [{ ...a.changes[0], path: 'x'.repeat(4097) }] })).toThrow();
  });

  it('rejects proxies without invoking traps at every structured input boundary', () => {
    const a = request(); let traps = 0;
    const proxy = <T extends object>(value: T): T => new Proxy(value, { get() { traps++; throw new Error('trap'); },
      getPrototypeOf() { traps++; throw new Error('trap'); }, ownKeys() { traps++; throw new Error('trap'); },
      getOwnPropertyDescriptor() { traps++; throw new Error('trap'); } });
    for (const value of [proxy(a), { ...a, changes: proxy(a.changes) }, { ...a, changes: [proxy(a.changes[0])] }]) {
      expect(() => hash(value)).toThrow('Invalid workflow snapshot save request');
    }
    expect(traps).toBe(0);
  });
  it('rejects source versions inconsistent with their observed saved head', () => {
    const a = request();
    for (const input of [{ ...a, sourceVersion: 'draft' },
      { ...a, sourceVersion: 'draft', expectedSavedHead: 'b'.repeat(40) },
      { ...a, sourceVersion: 'current', expectedSavedHead: 'b'.repeat(40) },
      { ...a, expectedSavedHead: 'a'.repeat(64) }]) expect(() => hash(input)).toThrow();
    expect(() => hash({ ...a, sourceVersion: 'draft', expectedSavedHead: oid })).not.toThrow();
    expect(() => hash({ ...a, expectedSavedHead: oid })).not.toThrow();
  });
  it('returns a frozen validated copy isolated from later caller mutations', () => {
    const a = request(); const result = fingerprintWorkflowSnapshotSaveRequest(a);
    const prior = result.inputHash;
    expect(result.request).not.toBe(a);
    expect(result.request.changes).not.toBe(a.changes);
    expect(result.request.changes[0]).not.toBe(a.changes[0]);
    expect(Object.isFrozen(result.request)).toBe(true);
    expect(Object.isFrozen(result.request.changes)).toBe(true);
    expect(result.request.changes.every(Object.isFrozen)).toBe(true);
    expect(Object.isFrozen(a)).toBe(false);
    expect(Object.isFrozen(a.changes)).toBe(false);
    expect(Object.isFrozen(a.changes[0])).toBe(false);
    a.changes[0].path = 'scripts/changed.sh';
    a.changes.reverse();
    a.actorId = 'actor-other';
    a.specJson = a.specJson.replace('"count":1', '"count":2');
    expect(result.inputHash).toBe(prior);
    expect(result.request.changes[0].path).toBe('scripts/a.sh');
    expect(result.request.changes[1].path).toBe('scripts/b.sh');
    expect(result.request.actorId).toBe('actor-1');
    expect(result.request.specJson).toContain('\"count\":1');
    expect(hash(result.request)).toBe(prior);
    expect(hash(a)).not.toBe(prior);
    expect(() => { (result.request.changes[0] as { path: string }).path = 'tamper'; }).toThrow();
  });

  it('binds sourceVersion independently when both source choices are valid', () => {
    const a = { ...request(), expectedSavedHead: oid };
    expect(hash(a)).not.toBe(hash({ ...a, sourceVersion: 'draft' }));
  });
  it('treats distinct Unicode path spellings as distinct with a pre-frozen request', () => {
    const a = request();
    const original = { ...a, changes: [{ ...a.changes[0], path: 'café' }] };
    const decomposed = { ...a, changes: [{ ...a.changes[0], path: 'cafe\u0301' }] };
    expect(hash(original)).not.toBe(hash(decomposed));
    Object.freeze(original.changes[0]); Object.freeze(original.changes); Object.freeze(original);
    const result = fingerprintWorkflowSnapshotSaveRequest(original);
    expect(result.inputHash).toBe(hash(original));
    expect(result.request).not.toBe(original);
    expect(result.request.changes).not.toBe(original.changes);
  });
  it('rejects a revoked proxy and an accessor array element without executing traps', () => {
    const a = request(); const revoked = Proxy.revocable(a, {}); revoked.revoke();
    expect(() => hash(revoked.proxy)).toThrow('Invalid workflow snapshot save request');
    let invoked = false; const changes = [a.changes[0]];
    Object.defineProperty(changes, '0', { enumerable: true, get() { invoked = true; return a.changes[0]; } });
    expect(() => hash({ ...a, changes })).toThrow();
    expect(invoked).toBe(false);
  });
  it('rejects parent/child write targets independent of order and operation', () => {
    const a = request();
    for (const first of ['create', 'replace']) for (const second of ['create', 'replace']) {
      const parent = { path: 'dir/file', operation: first, contentBase64: '', mode: '100644' };
      const child = { path: 'dir/file/nested', operation: second, contentBase64: '', mode: '100644' };
      expect(() => hash({ ...a, changes: [parent, child] })).toThrow();
      expect(() => hash({ ...a, changes: [child, parent] })).toThrow();
    }
    expect(() => hash({ ...a, changes: [{ path: 'file', operation: 'delete' },
      { path: 'file/child', operation: 'create', contentBase64: '', mode: '100644' }] })).not.toThrow();
    expect(() => hash({ ...a, changes: [{ path: 'file', operation: 'create', contentBase64: '', mode: '100644' },
      { path: 'file2/child', operation: 'create', contentBase64: '', mode: '100644' }] })).not.toThrow();
  });
  it('rejects only proven final Pack byte overflow from Pack-relative writes', () => {
    const a = request();
    const oneMiB = Buffer.alloc(1024 * 1024).toString('base64');
    const projectionBytes = Buffer.byteLength(fingerprintWorkflowSnapshotSaveRequest({ ...a, changes: [] }).projection.yaml);
    const changes = Array.from({ length: 8 }, (_, i) => ({ path: `file${i}`, operation: 'create', mode: '100644', contentBase64: oneMiB }));
    expect(() => hash({ ...a, changes })).toThrow();
    changes[7].contentBase64 = Buffer.alloc(1024 * 1024 - projectionBytes).toString('base64');
    expect(() => hash({ ...a, changes })).not.toThrow();
    changes[7].contentBase64 = Buffer.alloc(1024 * 1024 - projectionBytes + 1).toString('base64');
    expect(() => hash({ ...a, changes })).toThrow();
  });
  it('fails closed when definite final file count exceeds the candidate limit', () => {
    const a = request();
    const creates = Array.from({ length: 498 }, (_, i) => ({ path: `file${i}`, operation: 'create', mode: '100644', contentBase64: '' }));
    expect(() => hash({ ...a, changes: creates })).not.toThrow();
    expect(() => hash({ ...a, changes: [...creates, { path: 'file498', operation: 'create', mode: '100644', contentBase64: '' }] })).toThrow();
    expect(() => hash({ ...a, changes: [...creates, { path: 'old', operation: 'delete' }] })).not.toThrow();
  });

});
