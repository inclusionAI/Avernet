import { buildEditorGraph, editorTextDiff, editorYaml } from '@/domain/taskEscortParity/editor';
import { editorController } from '@/services/backendApi/harnessFlow/editorController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { createEditorService } from '@/services/taskEscortParity/editorService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const spec = {
  id: 'wf',
  title: '真实流程',
  version: '2',
  extension: { untouched: [false, null] },
  nodes: [
    {
      id: 'prepare',
      title: '准备',
      executor: { type: 'cli-script', command: 'scripts/a.sh', timeoutMs: 2400, custom: { a: 1 } },
      retry: { maxAttempts: 2 },
      unknown: { enabled: false },
    },
    { id: 'finish', title: '结束', dependsOn: ['prepare'], executor: { type: 'custom-executor' } },
  ],
};
const release = {
  deployNumber: 7,
  version: 2,
  action: 'deploy',
  isActive: true,
  gmtCreate: 1794902400,
  note: '真实发布',
  source: { kind: 'git' },
};
const pack = {
  workflowId: 'wf',
  packId: 'real-pack',
  baseRef: 'origin/wf/wf',
  baseCommit: 'a'.repeat(40),
  files: [{ path: 'scripts/a.sh', content: 'echo real', mode: '100755' }],
};
beforeEach(() => request.mockReset());

it('uses only verified GET routes, exact query identity and signal, no global injection', async () => {
  request.mockResolvedValue({});
  const params = { user_id: 'alice' },
    signal = new AbortController().signal;
  await editorController.workflow('wf/a', params, signal);
  await editorController.access('wf/a', params, signal);
  await editorController.history('wf/a', params, signal);
  await editorController.snapshot('wf/a', 7, params, signal);
  await editorController.diff('wf/a', 7, 9, params, signal);
  await editorController.scripts('wf/a', params, signal);
  const base = '/openapi/v1/harnessflow/api/workflows/wf%2Fa';
  expect(request.mock.calls.map(([url]) => url)).toEqual([
    base,
    `${base}/access`,
    `${base}/history`,
    `${base}/history/by-deploy/7`,
    `${base}/history/diff`,
    `${base}/pack-scripts`,
  ]);
  expect(
    request.mock.calls.every(
      ([, options]) =>
        options.method === 'GET' &&
        options.signal === signal &&
        options.injectUserId === false &&
        options.params.user_id === 'alice' &&
        options.data === undefined,
    ),
  ).toBe(true);
  expect(request.mock.calls[2][1].params).toEqual({ user_id: 'alice', limit: 20, releaseOnly: true });
  expect(request.mock.calls[4][1].params).toEqual({ user_id: 'alice', fromDeploy: 7, toDeploy: 9 });
  expect(request.mock.calls[5][1].headers).toEqual(expect.objectContaining({ 'Cache-Control': 'no-store' }));
});

it('maps the bare open spec losslessly including unknown node, executor and workflow extensions', async () => {
  request.mockResolvedValue(spec);
  const result = await createEditorService().workflow('wf', {});
  expect(result).toEqual(spec);
  expect(editorYaml(result)).toContain('timeoutMs: 2400');
  expect(editorYaml(result)).toContain('untouched:');
  expect(spec.nodes[0].unknown).toEqual({ enabled: false });
  const graph = buildEditorGraph(result.nodes);
  expect(graph.edges).toEqual([{ source: 'prepare', target: 'finish' }]);
  expect(graph.nodes.find((node) => node.id === 'finish')!.x).toBeGreaterThan(graph.nodes[0].x);
});

it.each([
  null,
  { ...spec, id: 'other' },
  { ...spec, nodes: null },
  { ...spec, nodes: [spec.nodes[0], spec.nodes[0]] },
  { ...spec, nodes: [{ id: 'bad', executor: { type: 1 } }] },
  { ...spec, nodes: [{ ...spec.nodes[1], dependsOn: [4] }] },
  { ...spec, version: -1 },
  { ...spec, version: Number.NaN },
])('rejects malformed or cross-workflow spec instead of inventing nodes: %j', async (value) => {
  request.mockResolvedValue(value);
  await expect(createEditorService().workflow('wf', {})).rejects.toThrow();
});

it('does not invent access flags and preserves denied requests as errors', async () => {
  const service = createEditorService();
  request.mockResolvedValueOnce({ workflowId: 'wf', canView: true, canEdit: false });
  expect(await service.access('wf', {})).toEqual({ workflowId: 'wf', canView: true, canEdit: false });
  request.mockResolvedValueOnce({ canView: true });
  await expect(service.access('wf', {})).rejects.toThrow();
  request.mockRejectedValueOnce(Object.assign(new Error('Forbidden'), { status: 403 }));
  await expect(service.scripts('wf', {})).rejects.toThrow(/Pack|权限/);
});

it('reads bounded release history, exact snapshots and diff sides without treating latest as active', async () => {
  const service = createEditorService();
  request.mockResolvedValueOnce({
    workflowId: 'wf',
    history: [release, { ...release, deployNumber: 8, action: 'edit' }],
    active: null,
  });
  expect(await service.history('wf', {})).toEqual({ workflowId: 'wf', history: [release], active: null });
  const snapshot = { workflowId: 'wf', ...release, specJson: JSON.stringify(spec) };
  request.mockResolvedValueOnce(snapshot);
  expect((await service.snapshot('wf', 7, {})).specJson).toBe(JSON.stringify(spec));
  request.mockResolvedValueOnce({ workflowId: 'wf', from: snapshot, to: { ...snapshot, deployNumber: 9 } });
  expect((await service.diff('wf', 7, 9, {})).to.deployNumber).toBe(9);
  request.mockResolvedValueOnce({ ...snapshot, deployNumber: 9 });
  await expect(service.snapshot('wf', 7, {})).rejects.toThrow();
  request.mockResolvedValueOnce({ workflowId: 'wf', from: snapshot, to: { ...snapshot, deployNumber: 8 } });
  await expect(service.diff('wf', 7, 9, {})).rejects.toThrow();
  request.mockResolvedValueOnce({ workflowId: 'wf', history: null, active: null });
  await expect(service.history('wf', {})).rejects.toThrow();
});

it('validates actual Pack files, safe unique paths, modes and Git provenance', async () => {
  const service = createEditorService();
  request.mockResolvedValueOnce(pack);
  expect(await service.scripts('wf', {})).toEqual(pack);
  for (const invalid of [
    { ...pack, workflowId: 'other' },
    { ...pack, baseCommit: 'made-up' },
    { ...pack, files: [{ path: '../secret', content: 'x', mode: '100644' }] },
    { ...pack, files: [pack.files[0], pack.files[0]] },
    { ...pack, files: [{ ...pack.files[0], content: null }] },
    { ...pack, files: [{ ...pack.files[0], mode: '120000' }] },
  ]) {
    request.mockResolvedValueOnce(invalid);
    await expect(service.scripts('wf', {})).rejects.toThrow();
  }
});

it('shows honest dependency warnings and finite layout for malformed references/cycles without creating nodes', () => {
  const nodes = [
    { id: 'a', dependsOn: ['b', 'missing'], executor: { type: 'x' } },
    { id: 'b', dependsOn: ['a'], executor: { type: 'x' } },
  ];
  const graph = buildEditorGraph(nodes);
  expect(graph.nodes.map((node) => node.id)).toEqual(['a', 'b']);
  expect(graph.nodes.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true);
  expect(graph.warnings.join(' ')).toMatch(/missing/);
  expect(graph.warnings.join(' ')).toMatch(/环/);
  expect(editorTextDiff('same', 'same')).toEqual([]);
  expect(editorTextDiff('a\nb\nc', 'a\nd\nc')).toEqual([
    { kind: 'removed', text: 'b' },
    { kind: 'added', text: 'd' },
  ]);
});

it('preserves the DB numeric version override without coercing it to the open string type', async () => {
  request.mockResolvedValueOnce({ ...spec, version: 6 });
  expect((await createEditorService().workflow('wf', {})).version).toBe(6);
});
