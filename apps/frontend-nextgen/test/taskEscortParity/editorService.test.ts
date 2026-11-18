import { buildEditorGraph, editorTextDiff, editorYaml } from '@/domain/taskEscortParity/editor';
import { editorController } from '@/services/backendApi/harnessFlow/editorController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { createEditorService } from '@/services/taskEscortParity/editorService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';

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
  expect(request.mock.calls.map(([, options]) => options.suppressErrorNotification === true)).toEqual([
    false,
    false,
    false,
    false,
    false,
    true,
  ]);
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

describe('script errors through the real backendRequest/fetch boundary', () => {
  const http = jest.requireActual<typeof import('@/services/backendApi/httpClient')>(
    '@/services/backendApi/httpClient',
  );
  const originalFetch = global.fetch;
  const originalStrategy = useLoginStrategyStore.getState().loginStrategy;
  const originMessage = '脚本请求来源校验失败，请检查访问地址或联系管理员。';
  const permissionMessage = '没有工作流编辑权限或 Pack 读取权限，无法查看脚本。';
  let fetchMock: jest.Mock;

  function respondWith(status: number, data: unknown) {
    // Only fetch is replaced: JSON parsing, error envelopes, notification and auth policies stay real.
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify(data), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
  }

  beforeEach(() => {
    request.mockImplementation(http.backendRequest);
    fetchMock = jest.fn();
    global.fetch = fetchMock;
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useExternalAuthStore.getState().reset();
    useLoginRedirectStore.getState().reset();
    useInviteCodeGateStore.getState().reset();
    useErrorNotifyStore.getState().reset();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    useLoginStrategyStore.getState().setLoginStrategy(originalStrategy);
    useExternalAuthStore.getState().reset();
    useLoginRedirectStore.getState().reset();
    useInviteCodeGateStore.getState().reset();
    useErrorNotifyStore.getState().reset();
  });

  it.each([
    { error: 'Origin not allowed', code: 'ORIGIN_NOT_ALLOWED' },
    { error: 'untrusted raw body', message: 'do not display this', code: 'ORIGIN_NOT_ALLOWED' },
  ])('maps only the explicit Origin code to a safe inline message: %j', async (data) => {
    respondWith(403, data);
    const signal = new AbortController().signal;
    await expect(createEditorService().scripts('wf/a', { user_id: 'alice' }, signal)).rejects.toThrow(originMessage);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/openapi/v1/harnessflow/api/workflows/wf%2Fa/pack-scripts?user_id=alice',
      expect.objectContaining({ method: 'GET', credentials: 'include', signal, body: undefined }),
    );
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it.each([
    { error: 'Forbidden' },
    { error: 'Origin not allowed' },
    { code: 'OTHER_DENIAL', message: 'Origin not allowed' },
    { code: 40300, data: { error_code: 'forbidden' } },
    null,
  ])('keeps generic 403 on the permission path, not message-text heuristics: %j', async (data) => {
    respondWith(403, data);
    await expect(createEditorService().scripts('wf', {})).rejects.toThrow(permissionMessage);
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(false);
  });

  it.each([
    [404, '未找到工作流或 Pack Git 快照。'],
    [409, 'Pack 快照存在分支冲突，暂无法安全读取。'],
    [503, 'Pack 脚本服务暂不可用，请稍后重试。'],
  ])('retains the existing safe scripts message for HTTP %s', async (status, message) => {
    respondWith(status, { error: 'untrusted raw body', code: 'ORIGIN_NOT_ALLOWED' });
    await expect(createEditorService().scripts('wf', {})).rejects.toThrow(message);
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });

  it('opts in only scripts, without cancelling other editor reads or already queued notifications', async () => {
    respondWith(403, { message: 'Forbidden' });
    const service = createEditorService();
    const reads = [
      () => service.workflow('wf', {}),
      () => service.access('wf', {}),
      () => service.history('wf', {}),
      () => service.snapshot('wf', 7, {}),
      () => service.diff('wf', 7, 9, {}),
    ];
    for (const read of reads) await expect(read()).rejects.toBeInstanceOf(http.BackendRequestError);
    const queued = [...useErrorNotifyStore.getState().queue];
    expect(queued).toHaveLength(reads.length);
    await expect(service.scripts('wf', {})).rejects.toThrow(permissionMessage);
    expect(useErrorNotifyStore.getState().queue).toEqual(queued);
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET' && options.body === undefined)).toBe(
      true,
    );
  });

  it.each(['oauth-provider', 'ace-gateway'] as const)('preserves the %s HTTP 401 login gate', async (strategy) => {
    useLoginStrategyStore.getState().setLoginStrategy(strategy);
    respondWith(401, { error: 'unauthorized' });
    await expect(createEditorService().scripts('wf', {})).rejects.toBeInstanceOf(http.AceLoginRedirectError);
    expect(useLoginRedirectStore.getState().pendingLogin).toMatchObject({
      mode: strategy === 'oauth-provider' ? 'prompt' : 'redirect',
    });
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });

  it.each([200, 403])('preserves the HTTP %s invitation gate and its silent error marker', async (status) => {
    respondWith(status, { code: 40300, data: { error_code: 'invite_code_required' } });
    await expect(createEditorService().scripts('wf', {})).rejects.toMatchObject({
      name: 'BackendRequestError',
      status,
      silent: true,
      alreadyHandled: true,
    });
    expect(useInviteCodeGateStore.getState().pendingPrompt).toBe(true);
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });

  it('preserves silent business errors while oauth has already determined the user is unauthenticated', async () => {
    useExternalAuthStore.getState().setUnauthenticated();
    respondWith(403, { error: 'Origin not allowed', code: 'ORIGIN_NOT_ALLOWED' });
    await expect(createEditorService().scripts('wf', {})).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 403,
      silent: true,
    });
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });
});
