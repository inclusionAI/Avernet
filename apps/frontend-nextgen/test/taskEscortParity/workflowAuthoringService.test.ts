import { createHash, webcrypto } from 'node:crypto';
import { authoringRecord, workflowAuthoringYaml } from '@/domain/taskEscortParity/workflowAuthoring';
import { WorkflowAiRecoveryError, WorkflowAuthoringOutcomeError } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringController } from '@/services/backendApi/harnessFlow/workflowAuthoringController';
import { backendRequest, type BackendRequestOptions } from '@/services/backendApi/httpClient';
import { createWorkflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const identity = { user_id: 'alice' }, other = { user_id: 'bob' };
const root = '/openapi/v1/harnessflow/api';
const a = 'a'.repeat(40), b = 'b'.repeat(40), t = '1'.repeat(64), u = '2'.repeat(64);
const requestId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const spec = { id: 'flow', title: '真实流程', version: '2', extension: { untouched: [false, null] },
  nodes: [{ id: 'run', title: '准备', executor: { type: 'cli-script', command: 'scripts/run.sh', custom: { x: 1 } }, _x: 20, _y: 40 },
    { id: 'done', dependsOn: ['run'], executor: { type: 'done' } }] };
const next = { ...spec, title: '实际修改', extension: { untouched: [false, null] } };
const encode = (text: string) => Buffer.from(text).toString('base64');
function files(value = spec, packId = 'flow') {
  return [{ path: 'workflow.pack.yaml', mode: '100644', contentBase64: encode(`id: ${packId}\nworkflows:\n- id: ${value.id}\n  file: workflows/${value.id}.yaml\n`) },
    { path: `workflows/${value.id}.yaml`, mode: '100644', contentBase64: encode(workflowAuthoringYaml(value)) },
    { path: 'scripts/run.sh', mode: '100755', contentBase64: encode('echo untouched\n') }];
}
function fixture(initialPackId: string | null = 'flow') {
  let rawPackId = initialPackId;
  let stored = spec, head = a, token = t;
  let lateMutation: (() => void) | undefined;
  const pack = () => ({ workflowId: stored.id, packId: rawPackId ?? stored.id, packPath: '', baseRef: `origin/wf/${stored.id}`, baseCommit: head, draftHead: head, versionFiles: files(stored, rawPackId ?? stored.id) });
  const draft = () => ({ workflowId: stored.id, packId: rawPackId, specJson: JSON.stringify(stored), draftToken: token });
  const transport = async (url: string, options: BackendRequestOptions): Promise<unknown> => {
    const input = options.data === undefined ? {} : authoringRecord(options.data);
    if (url.endsWith('/access')) return { workflowId: stored.id, canView: true, canEdit: true };
    if (url.endsWith('/editor-draft')) return draft();
    if (url.endsWith('/pack-version?version=draft')) return pack();
    if (url.endsWith('/pack-version?version=current')) return { ...pack(), baseRef: `refs/tags/deploy/${stored.id}/#1`, baseCommit: a, versionFiles: files(spec) };
    if (url.endsWith('/workflows/validate')) return { valid: true, issues: [], normalizedSpec: input.spec };
    if (url.endsWith('/unified-save') && options.method === 'GET') return { protocol: 'workflow-unified-save-v1' };
    if (url.endsWith('/unified-save') && options.method === 'POST') {
      stored = JSON.parse(String(input.specJson)) as typeof spec; head = b; token = u;
      lateMutation?.();
      return { requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: '3'.repeat(64), commit: head, draftToken: token } };
    }
    if (url.endsWith('/auto-heal/save')) {
      stored = input.spec as typeof spec; head = b; token = u; rawPackId = String(input.packId);
      return { ok: true, saved: true, workflowId: stored.id, packId: input.packId, branch: `wf/${stored.id}`, commit: b };
    }
    if (url.includes('/snapshot-v2/state?workflowId=')) return { protocol: 'editor-deployment-state-v1', workflowId: stored.id,
      packId: rawPackId ?? stored.id, canDeploy: true, savedCommit: head, activeCommit: a };
    if (url.endsWith('/snapshot-v2')) return { ok: true, deployed: true, requestId: input.requestId, workflowId: input.workflowId,
      packId: input.packId, deployNumber: 2, version: 3, tagName: `deploy/${input.workflowId}/#2` };
    throw new Error(`Unexpected request ${url}`);
  };
  request.mockImplementation(transport);
  return { service: createWorkflowAuthoringService(), transport, pack, draft,
    changeToken: () => { token = u; }, mutateAfterSave: (mutate: () => void) => { lateMutation = mutate; } };
}
function aiFixture() {
  let digest = '', output = workflowAuthoringYaml(next);
  const transport = async (url: string, options: BackendRequestOptions): Promise<unknown> => {
    const input = options.data === undefined ? {} : authoringRecord(options.data);
    if (url.endsWith('/capabilities')) return { available: true };
    if (url.endsWith('/prepare')) { digest = createHash('sha256').update(String(input.currentYaml)).digest('hex'); return { draftId: 'draft-1', receipt: 'signed-receipt', inputSpecDigest: digest }; }
    if (url.endsWith('/ai-drafts')) return { draftId: 'draft-1', status: 'running' };
    if (url.endsWith('/poll')) return { draftId: 'draft-1', workflowId: 'flow', inputSpecDigest: digest, status: 'completed', result: { yaml: output, summary: '真实摘要' } };
    if (url.endsWith('/cancel')) return { draftId: 'draft-1', workflowId: 'flow', inputSpecDigest: digest, status: 'cancelled' };
    if (url.endsWith('/validate')) return { valid: true, issues: [], normalizedSpec: input.spec };
    throw new Error(`Unexpected AI request ${url}`);
  };
  request.mockImplementation(transport);
  const service = createWorkflowAuthoringService();
  const start = () => service.startModification('flow', { requestId, prompt: '修改名称', currentYaml: workflowAuthoringYaml(spec) }, identity);
  return { service, start, transport, digest: () => digest, setOutput: (yaml: string) => { output = yaml; } };
}
const posts = (suffix: string) => request.mock.calls.filter(([url, options]) => url.endsWith(suffix) && options.method === 'POST');
const originalCryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
beforeAll(() => Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto }));
afterAll(() => { if (originalCryptoDescriptor) Object.defineProperty(globalThis, 'crypto', originalCryptoDescriptor); });
beforeEach(() => { request.mockReset(); jest.useRealTimers(); });
afterEach(() => jest.useRealTimers());

it('uses only same-origin latest routes, explicit header identity, AbortSignal and no auto retry/query identity', async () => {
  request.mockResolvedValue({}); const signal = new AbortController().signal;
  await workflowAuthoringController.prepareModification('flow/a', { requestId, mode: 'modify', prompt: 'p', currentYaml: 'y' }, identity, signal);
  await workflowAuthoringController.startModification('flow/a', { requestId, mode: 'modify', prompt: 'p', currentYaml: 'y', receipt: 'r' }, identity, signal);
  await workflowAuthoringController.pollModification('flow/a', 'draft/a', { receipt: 'r', currentYaml: 'y' }, identity, signal);
  await workflowAuthoringController.cancelModification('flow/a', 'draft/a', 'r', identity, signal);
  await workflowAuthoringController.draft('flow/a', identity, signal);
  await workflowAuthoringController.unifiedCapability('flow/a', identity, signal);
  expect(request.mock.calls.map(([url]) => url)).toEqual([`${root}/workflows/flow%2Fa/ai-drafts/prepare`, `${root}/workflows/flow%2Fa/ai-drafts`,
    `${root}/workflows/flow%2Fa/ai-drafts/draft%2Fa/poll`, `${root}/workflows/flow%2Fa/ai-drafts/draft%2Fa/cancel`,
    `${root}/workflows/flow%2Fa/editor-draft`, `${root}/workflows/flow%2Fa/unified-save`]);
  for (const [, options] of request.mock.calls) expect(options).toMatchObject({ signal, injectUserId: false, retryOnTransient: false,
    headers: { 'X-User-Id': 'alice', 'X-Pack-Version-CSRF': '1', 'Cache-Control': 'no-store' } });
  expect(request.mock.calls.every(([, options]) => options.params === undefined)).toBe(true);
});
it('rejects missing identity or already-aborted requests without I/O', async () => {
  const { service } = fixture();
  await expect(service.readBaseline('flow', { user_id: '' })).rejects.toThrow(/身份/);
  const abort = new AbortController(); abort.abort();
  await expect(service.createBlank({ workflowId: 'new', title: '新建' }, identity, abort.signal)).rejects.toThrow();
  expect(request).not.toHaveBeenCalled();
});
it('binds raw DB spec/version, NULL Pack and Git source in a real baseline', async () => {
  const { service } = fixture(null); const base = await service.readBaseline('flow', identity);
  expect(base).toMatchObject({ rawPackId: null, packId: 'flow', spec, draftToken: t, sourceCommit: a, expectedSavedHead: a });
  expect(base.spec.version).toBe('2');
  expect(() => { base.spec.title = 'tampered'; }).not.toThrow();
  await expect(service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toThrow(/基线/);
  expect(posts('/unified-save')).toHaveLength(0);
});
it('saves through the seven-field unified CAS contract and verifies NULL raw Pack without false unknown', async () => {
  const { service } = fixture(null), base = await service.readBaseline('flow', identity);
  const saved = await service.save({ workflowId: 'flow', spec: next, baseline: base }, identity);
  expect(saved).toMatchObject({ spec: next, commit: b, draftToken: u, dbSaved: true, gitSaved: true, baseline: { rawPackId: null, packId: 'flow' } });
  const input = posts('/unified-save')[0][1].data;
  expect(Object.keys(input).sort()).toEqual(['changes', 'expectedDraftToken', 'expectedSavedHead', 'requestId', 'sourceCommit', 'sourceVersion', 'specJson']);
  expect(input).toMatchObject({ sourceVersion: 'draft', sourceCommit: a, expectedSavedHead: a, expectedDraftToken: t, specJson: JSON.stringify(next), changes: [] });
  expect(posts('/workflows/save')).toHaveLength(0); expect(posts('/auto-heal/save')).toHaveLength(0);
  await expect(service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toThrow(/过期/);
});
it('refuses stale DB/token or wrong identity before any save POST', async () => {
  const f = fixture(), base = await f.service.readBaseline('flow', identity); f.changeToken();
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toThrow(/变化/);
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline: base }, other)).rejects.toThrow(/基线/);
  expect(posts('/unified-save')).toHaveLength(0);
});
it('never silently falls back when unified-save capability is unavailable or malformed', async () => {
  const f = fixture(), base = await f.service.readBaseline('flow', identity);
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/unified-save') ? { protocol: false } : f.transport(url, options));
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toThrow(/不会降级/);
  expect(posts('/unified-save')).toHaveLength(0); expect(posts('/workflows/save')).toHaveLength(0);
});
it.each(['request', 'outcome', 'hash', 'commit', 'token', 'git'])('rejects invalid unified receipt %s and blocks blind retry', async defect => {
  const f = fixture(), base = await f.service.readBaseline('flow', identity);
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    const value = await f.transport(url, options);
    if (!url.endsWith('/unified-save') || options.method !== 'POST') return value;
    const row = authoringRecord(value), evidence = authoringRecord(row.evidence);
    if (defect === 'request') row.requestId = 'other';
    if (defect === 'outcome') row.outcome = 'unknown';
    if (defect === 'hash') evidence.inputHash = 'made-up';
    if (defect === 'commit') evidence.commit = '';
    if (defect === 'token') evidence.draftToken = 'bad';
    if (defect === 'git') evidence.git = 'unknown';
    return row;
  });
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toBeInstanceOf(WorkflowAuthoringOutcomeError);
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline: base }, identity)).rejects.toThrow(/未确认/);
  expect(posts('/unified-save')).toHaveLength(1);
});
it('creates once with null head and requestId, then verifies exact database and Pack content', async () => {
  const f = fixture(); const created = await f.service.createBlank({ workflowId: 'new-flow', title: '新流程' }, identity);
  expect(created).toMatchObject({ id: 'new-flow', title: '新流程', nodes: [{ id: 'new-flow_start', title: '开始', executor: { type: 'done' } }] });
  expect(posts('/auto-heal/save')[0][1].data).toMatchObject({ workflowId: 'new-flow', packId: 'new-flow', createOnly: true, expectedSavedHead: null, requestId: expect.any(String) });
  await expect(f.service.createBlank({ workflowId: 'new-flow', title: '新流程' }, identity)).rejects.toThrow(/已创建/);
  expect(posts('/auto-heal/save')).toHaveLength(1);
});
it('does not replay a lost creation acknowledgement across dialog attempts', async () => {
  const f = fixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/auto-heal/save') ? Promise.reject(new Error('lost ack')) : f.transport(url, options));
  const input = { workflowId: 'new-flow', title: '保留表单' };
  await expect(f.service.createBlank(input, identity)).rejects.toMatchObject({ creationUncertain: true, workflowId: 'new-flow' });
  await expect(f.service.createBlank(input, identity)).rejects.toMatchObject({ creationUncertain: true });
  expect(posts('/auto-heal/save')).toHaveLength(1); expect(input.title).toBe('保留表单');
});
it('re-reads a confirmed creation after failed postflight without replaying create', async () => {
  const f = fixture(); let readsFail = true;
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/editor-draft') && readsFail ? Promise.reject(new Error('read failed')) : f.transport(url, options));
  const input = { workflowId: 'new-flow', title: '新流程' };
  await expect(f.service.createBlank(input, identity)).rejects.toMatchObject({ creationUncertain: true });
  readsFail = false; await expect(f.service.createBlank(input, identity)).resolves.toMatchObject({ id: 'new-flow' });
  expect(posts('/auto-heal/save')).toHaveLength(1);
});
it.each([403, 409])('preserves a definite creation %s rejection without fake success', async status => {
  const f = fixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/auto-heal/save') ? Promise.reject(Object.assign(new Error('rejected'), { status, data: { code: status === 403 ? 'FORBIDDEN' : 'WORKFLOW_ALREADY_EXISTS' } })) : f.transport(url, options));
  await expect(f.service.createBlank({ workflowId: 'new-flow', title: '保留' }, identity)).rejects.toMatchObject({ status });
});
it('reads actual saved/active file diffs and deploys only the verified, explicitly confirmed saved tree', async () => {
  const f = fixture(), baseline = await f.service.readBaseline('flow', identity);
  const receipt = await f.service.save({ workflowId: 'flow', spec: next, baseline }, identity);
  const review = await f.service.readPublishReview(receipt, identity);
  expect(review.changes).toEqual([expect.objectContaining({ path: 'workflows/flow.yaml', operation: 'replace', before: expect.stringContaining('真实流程'), after: expect.stringContaining('实际修改') })]);
  await expect(f.service.publish({ workflowId: 'flow', receipt, review, confirmedFileDiff: true }, identity)).resolves.toMatchObject({ deployed: true, deployNumber: 2 });
  expect(posts('/snapshot-v2')[0][1].data).toMatchObject({ candidateCommit: b, expectedDraftToken: u, expectedActiveCommit: a,
    snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true, autoRun: false, requestId: expect.any(String) });
  expect(posts('/auto-heal/deploy')).toHaveLength(0);
});
it('rejects forged publication receipts and changed reviews before POST', async () => {
  const f = fixture(), baseline = await f.service.readBaseline('flow', identity), receipt = await f.service.save({ workflowId: 'flow', spec: next, baseline }, identity);
  await expect(f.service.readPublishReview({ ...receipt }, identity)).rejects.toThrow(/回执/);
  const review = await f.service.readPublishReview(receipt, identity); review.changes.push({ path: 'forged', operation: 'create', before: null, after: 'x' });
  await expect(f.service.publish({ workflowId: 'flow', receipt, review, confirmedFileDiff: true }, identity)).rejects.toThrow(/审阅/);
  expect(posts('/snapshot-v2')).toHaveLength(0);
});
it('blocks a second publication when its first outcome is unknown', async () => {
  const f = fixture(), baseline = await f.service.readBaseline('flow', identity), receipt = await f.service.save({ workflowId: 'flow', spec: next, baseline }, identity);
  const review = await f.service.readPublishReview(receipt, identity);
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/snapshot-v2') ? Promise.reject(new Error('lost deployment ack')) : f.transport(url, options));
  const input = { workflowId: 'flow', receipt, review, confirmedFileDiff: true as const };
  await expect(f.service.publish(input, identity)).rejects.toBeInstanceOf(WorkflowAuthoringOutcomeError);
  await expect(f.service.publish(input, identity)).rejects.toThrow(/未确认/);
  expect(posts('/snapshot-v2')).toHaveLength(1);
});
it('uses prepare/receipt/POST poll, validates a candidate, and only applies in memory', async () => {
  const f = aiFixture(), pending = await f.start();
  expect(pending.receipt).toBe('signed-receipt'); expect(posts('/ai-drafts')[0][1].data).toMatchObject({ receipt: 'signed-receipt', currentYaml: workflowAuthoringYaml(spec) });
  const complete = await f.service.waitForModification(pending, identity);
  const applied = await f.service.applyModification(complete, spec, identity);
  expect(applied).toEqual(next); expect(applied.nodes[0]._x).toBe(20);
  expect(posts('/poll')[0][1].data).toEqual({ receipt: 'signed-receipt', currentYaml: workflowAuthoringYaml(spec) });
  expect(request.mock.calls.every(([url]) => !url.includes('smart-onboarding'))).toBe(true);
  expect(posts('/auto-heal/save')).toHaveLength(0); expect(posts('/unified-save')).toHaveLength(0);
});
it('retains a signed recovery job before a lost submit ack and never re-prepares or re-dispatches', async () => {
  const f = aiFixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/ai-drafts') ? Promise.reject(new Error('lost ack')) : f.transport(url, options));
  const failure: unknown = await f.start().catch(error => error);
  expect(failure).toBeInstanceOf(WorkflowAiRecoveryError);
  const recovered = (failure as WorkflowAiRecoveryError).job;
  expect(recovered?.receipt).toBe('signed-receipt');
  await expect(f.service.waitForModification(recovered!, identity)).resolves.toMatchObject({ status: 'completed' });
  await expect(f.start()).resolves.toMatchObject({ status: 'completed' });
  expect(posts('/prepare')).toHaveLength(1); expect(posts('/ai-drafts')).toHaveLength(1);
});
it('keeps a poll failure recoverable with the same task and no repeated generation', async () => {
  const f = aiFixture(), job = await f.start(); let fail = true;
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/poll') && fail ? Promise.reject(new Error('503')) : f.transport(url, options));
  await expect(f.service.waitForModification(job, identity)).rejects.toMatchObject({ job });
  fail = false; await expect(f.service.waitForModification(job, identity)).resolves.toMatchObject({ status: 'completed' });
  expect(posts('/ai-drafts')).toHaveLength(1);
});
it.each(['digest', 'draft', 'workflow', 'actor', 'status', 'result'])('rejects mismatched AI poll %s without applying anything', async defect => {
  const f = aiFixture(), job = await f.start();
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    const value = await f.transport(url, options); if (!url.endsWith('/poll')) return value;
    const row = authoringRecord(value);
    if (defect === 'digest') row.inputSpecDigest = 'f'.repeat(64);
    if (defect === 'draft') row.draftId = 'other';
    if (defect === 'workflow') row.workflowId = 'other';
    if (defect === 'actor') row.userId = 'bob';
    if (defect === 'status') row.status = 'invented';
    if (defect === 'result') row.result = null;
    return row;
  });
  await expect(f.service.pollModification(job, identity)).rejects.toThrow();
  expect(spec.title).toBe('真实流程');
});
it('rejects a wrong prepare digest before any dispatch', async () => {
  const f = aiFixture(); request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    const value = await f.transport(url, options); return url.endsWith('/prepare') ? { ...authoringRecord(value), inputSpecDigest: t } : value;
  });
  await expect(f.start()).rejects.toThrow(/摘要/); expect(posts('/ai-drafts')).toHaveLength(0);
});
it('blocks identity changes, forged handles and stale applied baselines', async () => {
  const f = aiFixture(), job = await f.start(), complete = await f.service.pollModification(job, identity);
  await expect(f.service.pollModification(job, other)).rejects.toThrow(/身份/);
  await expect(f.service.pollModification({ ...job }, identity)).rejects.toThrow(/身份/);
  await expect(f.service.applyModification(complete, { ...spec, title: 'concurrent edit' }, identity)).rejects.toThrow(/基线/);
});
it('accepts semantically identical YAML formatting but refuses AI ID/version changes', async () => {
  const f = aiFixture(); const job = await f.service.startModification('flow', { requestId, prompt: '修改', currentYaml: JSON.stringify(spec) }, identity);
  const complete = await f.service.pollModification(job, identity); await expect(f.service.applyModification(complete, spec, identity)).resolves.toMatchObject({ title: '实际修改' });
  const g = aiFixture(); g.setOutput(workflowAuthoringYaml({ ...spec, version: '3' }));
  const changed = await g.service.pollModification(await g.start(), identity);
  await expect(g.service.applyModification(changed, spec, identity)).rejects.toThrow(/版本/);
});
it('retries validation of a completed candidate without creating another AI task', async () => {
  const f = aiFixture(), complete = await f.service.pollModification(await f.start(), identity); let fail = true;
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/validate') && fail ? Promise.reject(new Error('503 validation')) : f.transport(url, options));
  await expect(f.service.applyModification(complete, spec, identity)).rejects.toThrow('503');
  fail = false; await expect(f.service.applyModification(complete, spec, identity)).resolves.toEqual(next);
  expect(posts('/ai-drafts')).toHaveLength(1);
});
it('cancels locally before an uncooperative late completed poll can escape or apply', async () => {
  const f = aiFixture(), job = await f.start(); let release: ((value: unknown) => void) | undefined;
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/poll') ? new Promise(resolve => { release = resolve; }) : f.transport(url, options));
  const polling = f.service.pollModification(job, identity).catch(error => error);
  const cancelled = await f.service.cancelModification(job, identity);
  expect(cancelled.status).toBe('cancelled'); expect(posts('/cancel')[0][1].data).toEqual({ receipt: 'signed-receipt' });
  release!(await f.transport(`${root}/workflows/flow/ai-drafts/draft-1/poll`, {}));
  expect(await polling).toEqual(expect.objectContaining({ message: expect.stringMatching(/取消/) }));
  await expect(f.service.applyModification(job, spec, identity)).rejects.toThrow(/取消/);
});
it('aborts polling promptly, never emitting a late result to onUpdate', async () => {
  const f = aiFixture(), job = await f.start(); jest.useFakeTimers();
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => url.endsWith('/poll') ? { draftId: job.draftId, workflowId: job.workflowId, inputSpecDigest: job.inputSpecDigest, status: 'running' } : f.transport(url, options));
  const abort = new AbortController(), onUpdate = jest.fn();
  const waiting = f.service.waitForModification(job, identity, { signal: abort.signal, intervalMs: 3000, onUpdate });
  await Promise.resolve(); await Promise.resolve(); abort.abort();
  await expect(waiting).rejects.toThrow(); const count = onUpdate.mock.calls.length;
  await jest.advanceTimersByTimeAsync(9000); expect(onUpdate).toHaveBeenCalledTimes(count);
});
it('uses the same signed create-mode jobs for new workflow candidates and explicit target rebinding', async () => {
  const f = aiFixture();
  const pending = await f.service.startGeneration({ workflowId: 'flow', currentYaml: workflowAuthoringYaml(spec), prompt: '新增能力' }, identity);
  const complete = await f.service.waitForGeneration(pending, identity);
  const candidate = await f.service.applyGeneration(complete, { workflowId: 'new-flow', title: '新流程' }, identity);
  expect(candidate).toMatchObject({ id: 'new-flow', title: '新流程', extension: next.extension });
  expect(posts('/ai-drafts')[0][1].data.mode).toBe('create');
  expect(posts('/auto-heal/save')).toHaveLength(0); expect(spec.id).toBe('flow');
});
it('requires a real editable AI source and uses signed cancel for generation', async () => {
  const f = aiFixture(); await expect(f.service.startGeneration({ prompt: 'generate' }, identity)).rejects.toThrow(/来源/);
  const pending = await f.service.startGeneration({ workflowId: 'flow', currentYaml: workflowAuthoringYaml(spec), prompt: 'generate' }, identity);
  await expect(f.service.cancelGeneration(pending, identity)).resolves.toMatchObject({ status: 'cancelled', cancellation: 'local-only' });
  await expect(f.service.pollGeneration(pending, identity)).rejects.toThrow(/取消/);
});
it('does not invent success from malformed validation or drop unknown fields', async () => {
  const f = aiFixture(); request.mockResolvedValue({ valid: true, issues: [], normalizedSpec: { ...spec, extension: {} } });
  await expect(f.service.validateYaml(workflowAuthoringYaml(spec), identity)).rejects.toThrow(/字段/);
  request.mockResolvedValue({ valid: false, issues: [{ path: 'nodes', message: 'invalid', severity: 'error' }], normalizedSpec: null });
  await expect(f.service.validateYaml(workflowAuthoringYaml(spec), identity)).resolves.toMatchObject({ valid: false, spec: null, errors: [{ path: 'nodes', message: 'invalid', severity: 'error' }] });
});

it('serializes exact create, unified-save and snapshot publication wire contracts without legacy endpoints', async () => {
  request.mockResolvedValue({}); const signal = new AbortController().signal;
  await workflowAuthoringController.create({ workflowId: 'flow', packId: 'flow', spec, createOnly: true, expectedSavedHead: null, requestId }, identity, signal);
  await workflowAuthoringController.unifiedSave('flow', { requestId, sourceVersion: 'draft', sourceCommit: a, expectedSavedHead: a,
    expectedDraftToken: t, specJson: JSON.stringify(spec), changes: [] }, identity, signal);
  await workflowAuthoringController.packVersion('flow', 'current', identity, signal);
  await workflowAuthoringController.deploymentState('flow', identity, signal);
  await workflowAuthoringController.publish({ workflowId: 'flow', packId: 'flow', candidateCommit: b, expectedDraftToken: u,
    expectedActiveCommit: a, requestId, snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true, autoRun: false }, identity, signal);
  expect(request.mock.calls.map(([url]) => url)).toEqual([`${root}/auto-heal/save`, `${root}/workflows/flow/unified-save`,
    `${root}/workflows/flow/pack-version?version=current`, `${root}/auto-heal/editor-deploy/snapshot-v2/state?workflowId=flow`,
    `${root}/auto-heal/editor-deploy/snapshot-v2`]);
  expect(request.mock.calls[0][1].data).toMatchObject({ createOnly: true, expectedSavedHead: null, requestId });
  expect(request.mock.calls[1][1].data).not.toHaveProperty('actorId');
  expect(request.mock.calls[4][1].data).toMatchObject({ candidateCommit: b, expectedDraftToken: u, requestId, autoRun: false });
  expect(request.mock.calls.every(([, options]) => options.signal === signal && options.injectUserId === false && options.params === undefined)).toBe(true);
});
it('maps actual clone candidates and source preview with Git provenance', async () => {
  const service = createWorkflowAuthoringService();
  request.mockResolvedValueOnce({ data: [{ workflowId: 'source', title: '来源流程', packId: null, updatedAt: 1794902400000 }] });
  await expect(service.listCloneCandidates(identity)).resolves.toEqual([{ workflowId: 'source', title: '来源流程', packId: null, updatedAt: 1794902400000 }]);
  request.mockResolvedValueOnce({ sourceWorkflowId: 'source', sourcePackId: 'source-pack', sourceTitle: '来源流程', baseCommit: a, scriptCount: 3 });
  await expect(service.readClonePreview('source', identity)).resolves.toMatchObject({ baseCommit: a, scriptCount: 3 });
  expect(request.mock.calls[1][1].data).toEqual({ sourceWorkflowId: 'source' });
  expect(request.mock.calls[1][0]).toBe(`${root}/workflows/clone/preview`);
});
it('rejects duplicate clone candidates and mismatched source previews', async () => {
  const service = createWorkflowAuthoringService();
  const candidate = { workflowId: 'source', title: '来源流程', packId: 'source-pack', updatedAt: 1 };
  request.mockResolvedValueOnce({ data: [candidate, candidate] });
  await expect(service.listCloneCandidates(identity)).rejects.toThrow(/重复/);
  request.mockResolvedValueOnce({ sourceWorkflowId: 'other', sourcePackId: 'source-pack', sourceTitle: '来源流程', baseCommit: a, scriptCount: 3 });
  await expect(service.readClonePreview('source', identity)).rejects.toThrow(/来源/);
});
it('clones through the script-preserving endpoint and independently verifies the target snapshot', async () => {
  const f = fixture(); const target = { ...spec, id: 'new-flow', title: '独立工作流' };
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    if (!url.endsWith('/workflows/clone')) return f.transport(url, options);
    const input = authoringRecord(options.data);
    await f.transport(`${root}/auto-heal/save`, { data: { workflowId: input.targetWorkflowId, packId: input.targetWorkflowId, spec: target } });
    return { sourceWorkflowId: input.sourceWorkflowId, sourcePackId: 'flow', sourceTitle: '来源流程', baseCommit: a,
      scriptCount: 1, targetWorkflowId: input.targetWorkflowId, targetPackId: input.targetWorkflowId, branch: `wf/${input.targetWorkflowId}`, commit: b };
  });
  const result = await f.service.createFromExisting({ workflowId: 'new-flow', title: '独立工作流', sourceWorkflowId: 'flow' }, identity);
  expect(result).toMatchObject({ targetWorkflowId: 'new-flow', targetPackId: 'new-flow', sourceWorkflowId: 'flow', commit: b, scriptCount: 1 });
  expect(posts('/workflows/clone')[0][1].data).toEqual({ sourceWorkflowId: 'flow', targetWorkflowId: 'new-flow', targetTitle: '独立工作流' });
  expect(posts('/workflows/save')).toHaveLength(0);
  expect(posts('/auto-heal/save')).toHaveLength(0);
  expect(spec.id).toBe('flow');
});
it('locks an unknown clone outcome across repeated calls instead of falling back to spec upsert', async () => {
  const f = fixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/workflows/clone') ? Promise.reject(new Error('unknown clone result')) : f.transport(url, options));
  const input = { workflowId: 'new-flow', title: '保留克隆输入', sourceWorkflowId: 'flow' };
  await expect(f.service.createFromExisting(input, identity)).rejects.toMatchObject({ creationUncertain: true, operation: 'create' });
  await expect(f.service.createFromExisting(input, identity)).rejects.toMatchObject({ creationUncertain: true });
  expect(posts('/workflows/clone')).toHaveLength(1); expect(posts('/workflows/save')).toHaveLength(0);
});
it('refuses denied edit permission before reading a baseline or writing', async () => {
  const f = fixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/access') ? { workflowId: 'flow', canView: true, canEdit: false } : f.transport(url, options));
  await expect(f.service.readBaseline('flow', identity)).rejects.toMatchObject({ status: 403 });
  expect(request).toHaveBeenCalledTimes(1);
  expect(posts('/unified-save')).toHaveLength(0);
});
it('does not dispatch AI when the real capability reports unavailable', async () => {
  const f = aiFixture(); request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/capabilities') ? { available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' } : f.transport(url, options));
  await expect(f.start()).rejects.toThrow(/不可用/);
  expect(posts('/prepare')).toHaveLength(0); expect(posts('/ai-drafts')).toHaveLength(0);
});
it('polls dispatch-uncertain and pending states to a completed result without any resubmission', async () => {
  const f = aiFixture(), job = await f.start(); let count = 0;
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    const value = await f.transport(url, options);
    if (!url.endsWith('/poll')) return value;
    count += 1;
    return count < 3 ? { draftId: job.draftId, workflowId: 'flow', inputSpecDigest: job.inputSpecDigest,
      status: count === 1 ? 'dispatch_uncertain' : 'pending' } : value;
  });
  const updates: string[] = [];
  await expect(f.service.waitForModification(job, identity, { intervalMs: 1, timeoutMs: 1000, onUpdate: value => updates.push(value.status) })).resolves.toMatchObject({ status: 'completed' });
  expect(updates).toEqual(['dispatch_uncertain', 'pending', 'completed']);
  expect(posts('/prepare')).toHaveLength(1); expect(posts('/ai-drafts')).toHaveLength(1);
});
it('returns failed AI terminal state faithfully without treating it as a successful candidate', async () => {
  const f = aiFixture(), job = await f.start();
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/poll') ? {
    draftId: job.draftId, workflowId: 'flow', inputSpecDigest: job.inputSpecDigest, status: 'failed', error: 'GENERATION_FAILED',
  } : f.transport(url, options));
  const failed = await f.service.waitForModification(job, identity);
  expect(failed).toMatchObject({ status: 'failed', error: 'GENERATION_FAILED', result: undefined });
  await expect(f.service.applyModification(failed, spec, identity)).rejects.toThrow(/尚未完成/);
});
it('marks timeout as a local fence and prevents future application of that attempt', async () => {
  const f = aiFixture(), job = await f.start();
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/poll') ? {
    draftId: job.draftId, workflowId: 'flow', inputSpecDigest: job.inputSpecDigest, status: 'running',
  } : f.transport(url, options));
  await expect(f.service.waitForModification(job, identity, { intervalMs: 10, timeoutMs: 1 })).rejects.toMatchObject({ name: 'TimeoutError' });
  await expect(f.service.applyModification(job, spec, identity)).rejects.toThrow(/取消/);
  expect(posts('/ai-drafts')).toHaveLength(1);
});
it('does not silently strip extension data on save validation', async () => {
  const f = fixture(), baseline = await f.service.readBaseline('flow', identity);
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/validate') ? {
    valid: true, issues: [], normalizedSpec: { ...next, extension: { untouched: [] } },
  } : f.transport(url, options));
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline }, identity)).rejects.toThrow(/字段/);
  expect(posts('/unified-save')).toHaveLength(0);
});
it('fails postflight when Git changed a script that was not part of the candidate', async () => {
  const f = fixture(), baseline = await f.service.readBaseline('flow', identity); let saved = false;
  request.mockImplementation(async (url: string, options: BackendRequestOptions) => {
    const value = await f.transport(url, options);
    if (url.endsWith('/unified-save') && options.method === 'POST') saved = true;
    if (url.endsWith('/pack-version?version=draft') && saved) {
      const snapshot = authoringRecord(value);
      const versionFiles = snapshot.versionFiles as Array<{ path: string; contentBase64: string; mode: string }>;
      return { ...snapshot, versionFiles: versionFiles.map(file => file.path === 'scripts/run.sh' ? { ...file, contentBase64: encode('echo unexpected\n') } : file) };
    }
    return value;
  });
  await expect(f.service.save({ workflowId: 'flow', spec: next, baseline }, identity)).rejects.toMatchObject({ operation: 'save', cause: expect.objectContaining({ message: expect.stringContaining('Pack') }) });
  expect(posts('/unified-save')).toHaveLength(1);
  await expect(f.service.readBaseline('flow', identity)).rejects.toThrow(/未确认/);
});
it('recovers a create-mode generation from its signed error job without a new prepare or submit', async () => {
  const f = aiFixture();
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/ai-drafts') ? Promise.reject(new Error('lost generation ack')) : f.transport(url, options));
  const failure: unknown = await f.service.startGeneration({ workflowId: 'flow', currentYaml: workflowAuthoringYaml(spec), prompt: '新增能力' }, identity).catch(cause => cause);
  expect(failure).toBeInstanceOf(WorkflowAiRecoveryError);
  const recovery = (failure as WorkflowAiRecoveryError).job;
  expect(recovery?.receipt).toBe('signed-receipt');
  const resumed = await f.service.resumeGeneration(recovery!, identity);
  const candidate = await f.service.applyGeneration(resumed, { workflowId: 'new-flow' }, identity);
  expect(candidate.id).toBe('new-flow');
  expect(posts('/prepare')).toHaveLength(1); expect(posts('/ai-drafts')).toHaveLength(1);
});
it('permits validation-only retries on a completed generation handle without dispatching again', async () => {
  const f = aiFixture();
  const pending = await f.service.startGeneration({ workflowId: 'flow', currentYaml: workflowAuthoringYaml(spec), prompt: '新增能力' }, identity);
  const completed = await f.service.waitForGeneration(pending, identity);
  let fail = true;
  request.mockImplementation((url: string, options: BackendRequestOptions) => url.endsWith('/validate') && fail ? Promise.reject(new Error('503 validation')) : f.transport(url, options));
  await expect(f.service.applyGeneration(completed, { workflowId: 'new-flow' }, identity)).rejects.toThrow('503');
  fail = false;
  await expect(f.service.applyGeneration(completed, { workflowId: 'new-flow' }, identity)).resolves.toMatchObject({ id: 'new-flow' });
  expect(posts('/prepare')).toHaveLength(1); expect(posts('/ai-drafts')).toHaveLength(1);
});
