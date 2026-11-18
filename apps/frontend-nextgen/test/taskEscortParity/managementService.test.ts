import { managementJobLink } from '@/domain/taskEscortParity/management';
import { managementController as api } from '@/services/backendApi/harnessFlow/managementController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { managementService as service } from '@/services/taskEscortParity/managementService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const signal = new AbortController().signal;
const source = {
  kind: 'auto_optimization',
  deployNumber: 7,
  baselineDeployNumber: 3,
  taskId: 'task-1',
  revision: 2,
  automationRoundId: 'round-1',
  actorId: 'owner-1',
  effectiveAtMs: 1794873600000,
  restoredFromDeployNumber: null,
};
const item = {
  version: 3,
  deployNumber: 7,
  tagName: 'release-3',
  action: 'deploy',
  fromDeployNumber: null,
  note: 'release',
  botId: null,
  ownerId: 'owner-1',
  isActive: true,
  gmtCreate: 1794873600,
  source,
};
const policy = {
  workflowId: 'wf',
  enabled: false,
  autoDraft: true,
  dailyTime: '10:30',
  timezone: 'Asia/Shanghai',
  executionEnvironment: 'pre',
  minimumImpact: 'high',
  notificationRecipients: ['owner-1'],
  version: 4,
  notificationVersion: 3,
  updatedAtMs: 1794873600000,
  dailyConfigured: true,
  source: 'database',
};
beforeEach(() => request.mockReset());

it.each([
  ['access', '/workflows/w%2Ff/access', []],
  ['history', '/workflows/w%2Ff/history', []],
  ['version', '/workflows/w%2Ff/history/3', [3]],
  ['deploy', '/workflows/w%2Ff/history/by-deploy/7', [7]],
  ['permissions', '/workflows/w%2Ff/bot-permissions', []],
  ['notification', '/workflows/w%2Ff/notification-config', []],
  ['callbacks', '/workflows/w%2Ff/callback-configs', []],
  ['policy', '/task-guard/workflows/w%2Ff/auto-analysis', []],
  ['status', '/task-guard/workflows/w%2Ff/automation-status', []],
] as const)(
  '%s wires only GET through the existing gateway with explicit actor and cancellation',
  async (method, path, args) => {
    request.mockResolvedValue(null);
    await (api[method] as (...args: unknown[]) => Promise<unknown>)('w/f', 'actor', ...args, signal);
    expect(request).toHaveBeenCalledWith(`/openapi/v1/harnessflow/api${path}`, {
      method: 'GET',
      params: { user_id: 'actor', ...(method === 'history' ? { limit: 20 } : {}) },
      signal,
      injectUserId: false,
    });
  },
);

it('uses the global facade array and exact record filters/cursor, never a guessed workflow facade route', async () => {
  request.mockResolvedValue([]);
  await api.facades('actor', signal);
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/harnessflow/api/facades', {
    method: 'GET',
    params: { user_id: 'actor' },
    signal,
    injectUserId: false,
  });
  const query = { kind: 'analysis', period: '7d', status: 'blocked', limit: 20, cursor: 'opaque+/=' } as const;
  await api.records('wf', 'actor', query, signal);
  expect(request).toHaveBeenLastCalledWith('/openapi/v1/harnessflow/api/task-guard/workflows/wf/automation-records', {
    method: 'GET',
    params: { ...query, user_id: 'actor' },
    signal,
    injectUserId: false,
  });
});

it('maps active history, provenance and seconds without inferring first row as current', async () => {
  request.mockResolvedValue({ history: [item], active: item });
  const value = await service.history('wf', 'actor', signal);
  expect(value.history[0]).toMatchObject({ createdAtMs: 1794873600000, source, isActive: true });
  expect(value.active?.deployNumber).toBe(7);
});

it('maps snapshots by identity and drops secrets even inside nested JSON', async () => {
  request.mockResolvedValue({
    workflowId: 'wf',
    version: 3,
    deployNumber: 7,
    tagName: null,
    action: 'deploy',
    note: null,
    gmtCreate: 1794873600,
    specJson: JSON.stringify({ nodes: [{ id: 'n', appSecret: 'TOP_SECRET', nested: { token: 'TOKEN_SECRET' } }] }),
  });
  const value = await service.version('wf', 'actor', 3, signal);
  expect(value.createdAtMs).toBe(1794873600000);
  expect(value.specJson).toContain('n');
  expect(JSON.stringify(value)).not.toMatch(/TOP_SECRET|TOKEN_SECRET/);
  await expect(service.version('other', 'actor', 3, signal)).rejects.toThrow(/协议|身份/);
});

it('does not cache notification or callback secrets (including masked reusable-looking values)', async () => {
  request.mockResolvedValue({
    workflowId: 'wf',
    robotCode: 'robot',
    appSecret: 'TOP_SECRET',
    onFailureUsers: [{ userId: 'u', name: 'User' }],
    onFailureGroups: [{ openConversationId: 'g' }],
    onFailureMessageTitle: 'Failure',
    onFailureMessageIncludeRunLink: true,
    notifyOn: ['node_failed'],
    eventOptions: { node_failed: { title: 'Node failed', includeNodeOutput: true, maxOutputFields: 3 } },
  });
  const notification = await service.notification('wf', 'actor', signal);
  expect(notification).toMatchObject({
    hasSecret: true,
    robotCode: 'robot',
    eventOptions: { node_failed: { title: 'Node failed', includeNodeOutput: true, maxOutputFields: 3 } },
  });
  expect(JSON.stringify(notification)).not.toContain('TOP_SECRET');
  expect(notification).not.toHaveProperty('appSecret');
  request.mockResolvedValue([
    {
      id: 1,
      configId: 'cb',
      workflowId: 'wf',
      name: 'Callback',
      url: 'https://example.test/hook?token=URL_SECRET',
      secret: '******',
      enabled: false,
      notifyOn: ['workflow_failed'],
      timeoutMs: 5000,
      maxRetries: 2,
      retryDelayMs: 1000,
      includeNodeOutput: true,
      gmtCreate: 1794873600,
      gmtModified: 1794873601,
    },
  ]);
  const callbacks = await service.callbacks('wf', 'actor', signal);
  expect(callbacks[0]).toMatchObject({ hasSecret: true, createdAtMs: 1794873600000 });
  expect(callbacks[0]).not.toHaveProperty('secret');
  expect(JSON.stringify(callbacks)).not.toContain('URL_SECRET');
});

it('strictly maps policy and record fields, preserves unknown optional policy fields and real links', async () => {
  request.mockResolvedValue(policy);
  expect(await service.policy('wf', 'actor', signal)).toMatchObject(policy);
  request.mockResolvedValue({
    jobs: [
      {
        id: 'job',
        kind: 'auto_run',
        status: 'done',
        attempts: 2,
        reason: 'PARTIAL_ANALYSIS',
        createdAtMs: 1794873600000,
        flowId: 'run/1',
        analysisId: 'analysis/1',
        complete: false,
      },
    ],
    nextCursor: 'cursor-2',
  });
  const records = await service.records(
    'wf',
    'actor',
    { kind: 'analysis', period: 'today', status: 'all', limit: 20 },
    signal,
  );
  expect(records.nextCursor).toBe('cursor-2');
  expect(records.jobs[0]).toMatchObject({ createdAtMs: 1794873600000, complete: false, flowId: 'run/1' });
  const link = new URL(managementJobLink('wf', records.jobs[0])!, 'http://localhost');
  expect(link.pathname).toBe('/evolution-factory/task-escort');
  expect(link.searchParams.get('runId')).toBe('run/1');
  expect(link.searchParams.get('analysisId')).toBe('analysis/1');
});

it('filters the global facade list to the selected workflow and handles true empty/null', async () => {
  request.mockResolvedValue([
    { workflowId: 'other', command: '/other', packId: null, remark: null },
    { workflowId: 'wf', command: '/run', packId: 'p', remark: 'Run' },
  ]);
  expect(await service.facades('wf', 'actor', signal)).toEqual([
    { workflowId: 'wf', command: '/run', packId: 'p', remark: 'Run' },
  ]);
  request.mockResolvedValue(null);
  expect(await service.notification('wf', 'actor', signal)).toBeNull();
  request.mockResolvedValue([]);
  expect(await service.permissions('wf', 'actor', signal)).toEqual([]);
});

it.each([
  ['access', { canView: true, canEdit: 1 }],
  ['history', { history: [], active: undefined }],
  ['permissions', {}],
  ['permissions', [{ id: 1, botId: null, botOwnerId: 'a', canView: 2, canExecute: 0, canEdit: 1 }]],
  ['facades', { data: [] }],
  ['callbacks', [{ workflowId: 'other' }]],
  ['notification', {}],
  ['policy', { ...policy, enabled: 'true' }],
  ['policy', { ...policy, workflowId: 'other' }],
  ['status', { coverage: {} }],
] as const)('rejects malformed %s instead of turning it into empty or privileged success', async (method, payload) => {
  request.mockResolvedValue(payload);
  await expect((service[method] as (...args: unknown[]) => Promise<unknown>)('wf', 'actor', signal)).rejects.toThrow();
});

it('fails closed on missing actor, hides server error bodies, and preserves safe 403/abort semantics', async () => {
  await expect(service.access('wf', '', signal)).rejects.toThrow(/身份/);
  expect(request).not.toHaveBeenCalled();
  request.mockRejectedValue({ status: 403, message: 'TOP_SECRET' });
  await expect(service.permissions('wf', 'actor', signal)).rejects.toThrow(/403/);
  request.mockRejectedValue(new Error('TOP_SECRET'));
  await expect(service.notification('wf', 'actor', signal)).rejects.not.toThrow('TOP_SECRET');
  const aborted = Object.assign(new Error('aborted'), { name: 'AbortError' });
  request.mockRejectedValue(aborted);
  await expect(service.access('wf', 'actor', signal)).rejects.toMatchObject({
    name: 'AbortError',
    message: '请求已取消',
  });
  expect(Object.keys(api).sort()).toEqual(
    [
      'access',
      'callbacks',
      'deploy',
      'facades',
      'history',
      'notification',
      'permissions',
      'policy',
      'records',
      'status',
      'version',
    ].sort(),
  );
});

it('does not leak a credential even when the upstream error claims to be a protocol failure', async () => {
  request.mockRejectedValue(new Error('协议失败 appSecret=TOP_SECRET'));
  await expect(service.notification('wf', 'actor', signal)).rejects.not.toThrow('TOP_SECRET');
});

it.each(['null', '"scalar"', '[]', '{invalid'])(
  'rejects a malformed or non-object workflow snapshot: %s',
  async (specJson) => {
    request.mockResolvedValue({
      workflowId: 'wf',
      version: 3,
      deployNumber: 7,
      tagName: null,
      action: 'deploy',
      note: null,
      gmtCreate: 1794873600,
      specJson,
    });
    await expect(service.version('wf', 'actor', 3, signal)).rejects.toThrow(/协议/);
  },
);

it('strictly maps status snapshot coverage, round and retryable jobs without inventing counts', async () => {
  const entry = {
    id: 'notice',
    kind: 'auto_notice',
    status: 'blocked',
    attempts: 1,
    reason: 'NOTIFICATION_REJECTED',
    taskId: 'task-2',
    createdAtMs: 1794873600000,
  };
  const round = {
    id: 'round',
    total: 5,
    batches: 3,
    completedBatches: 1,
    deferred: { SOURCE_CHANGED: 2 },
    fromMs: 1794700800000,
    untilMs: 1794873600000,
    evidenceCutoffMs: 1794873600000,
  };
  request.mockResolvedValue({
    schemaVersion: 'workflow-automation/v1',
    workflowId: 'wf',
    lastScannedAtMs: 1794873600000,
    pauseReason: null,
    coverage: { total: 11, pending: 2, failed: 3, partial: 1, unknown: 5 },
    jobs: [entry],
    retryableJobs: [entry],
    round,
  });
  const status = await service.status('wf', 'actor', signal);
  expect(status).toMatchObject({
    coverage: { total: 11, pending: 2, failed: 3, partial: 1, unknown: 5 },
    round,
    retryableJobs: [{ id: 'notice', taskId: 'task-2' }],
  });
  const link = new URL(managementJobLink('wf', status.jobs[0])!, 'http://localhost');
  expect(link.searchParams.get('repairTaskId')).toBe('task-2');
  expect(link.searchParams.get('view')).toBe('issues');
  expect(managementJobLink('wf', { ...status.jobs[0], taskId: null })).toBeNull();
});

it.each([401, 404, 409, 503, 500])(
  'safely displays HTTP %i without leaking body or inventing success',
  async (status) => {
    request.mockRejectedValue({ response: { status }, message: 'secret-body' });
    await expect(service.history('wf', 'actor', signal)).rejects.toThrow(String(status));
    await expect(service.history('wf', 'actor', signal)).rejects.not.toThrow('secret-body');
  },
);

it.each([
  { jobs: [], nextCursor: undefined },
  { jobs: [], nextCursor: '' },
  { jobs: {}, nextCursor: null },
])('rejects malformed records %o', async (payload) => {
  request.mockResolvedValue(payload);
  await expect(
    service.records('wf', 'actor', { kind: 'notification', period: 'all', status: 'attention', limit: 8 }, signal),
  ).rejects.toThrow(/协议/);
});
