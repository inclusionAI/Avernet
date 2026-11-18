import type { TaskEscortWorkspaceApi } from '@/services/taskEscortWorkspace';
import { createTaskEscortWorkspaceService } from '@/services/taskEscortWorkspace';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

jest.mock('@/services/taskEscort', () => ({
  taskEscortService: {
    listWorkflows: jest.fn(),
    listFlowRuns: jest.fn(),
    getFlowRun: jest.fn(),
  },
}));

function api(): jest.Mocked<TaskEscortWorkspaceApi> {
  return {
    listWorkflows: jest
      .fn()
      .mockResolvedValue([{ workflowId: 'wf-real', title: 'Real workflow', packId: null, updatedAt: 0 }]),
    listFlowRuns: jest.fn().mockResolvedValue([]),
    getFlowRun: jest.fn(),
  };
}
const rawRun = {
  flow_id: 'run-real',
  workflow_id: 'wf-real',
  workflow_title: 'Real workflow',
  status: 'succeeded',
  started_at: 1794873600000,
  succeeded_count: 2,
  failed_count: 0,
  total_duration_ms: 1250,
  total_token_usage: 75,
  triggered_by: 'schedule',
  origin_session_id: 'session-real',
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe('Task Escort Workspace live-only runtime', () => {
  it('imports the service factory directly in the formal hook, avoiding prototype barrel reexports', () => {
    const source = readFileSync(resolve(__dirname, '../../src/hooks/useTaskEscortWorkspace.ts'), 'utf8');
    expect(source).toMatch(/from\s*['"]@\/services\/taskEscortWorkspace\/service['"]/);
    expect(source).not.toMatch(/from\s*['"]@\/services\/taskEscortWorkspace['"]/);
  });

  it('does not import fixtures or demo actions into the formal service', () => {
    const source = readFileSync(resolve(__dirname, '../../src/services/taskEscortWorkspace/service.ts'), 'utf8');
    expect(source).not.toMatch(/(?:from\s*|import\s*\(|require\s*\()\s*['"][^'"]*(?:fixtures|demoActions)/);
  });

  it.each(['demo', 'live', 'invalid' as never, null as never, undefined] as const)(
    'normalizes requestedMode=%s to an empty live snapshot and real API reads',
    async (mode) => {
      const gateway = api();
      gateway.listFlowRuns.mockResolvedValue([rawRun]);
      const service = createTaskEscortWorkspaceService(mode, gateway);
      expect(service.mode).toBe('live');
      expect(service.getSnapshot()).toMatchObject({ workflows: [], runs: [], issues: [], experiences: [] });
      expect(service.support.editor).toMatchObject({ available: false, reason: expect.any(String) });
      await service.load();
      expect(gateway.listWorkflows).toHaveBeenCalledTimes(1);
      expect(gateway.listFlowRuns).toHaveBeenCalledWith({ limit: 100 });
      expect(service.getSnapshot().workflows.map((workflow) => workflow.id)).toEqual(['wf-real']);
      expect(service.getSnapshot().runs.map((run) => run.id)).toEqual(['run-real']);
      expect(service.getSnapshot()).toMatchObject({ issues: [], experiences: [], loading: false, error: null });
      expect(gateway.getFlowRun).not.toHaveBeenCalled();
    },
  );

  it.each(['demo', 'live', 'invalid' as never] as const)('rejects every write even for requestedMode=%s', (mode) => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService(mode, gateway);
    const original = service.getSnapshot();
    for (const id of ['i1', 'missing']) {
      for (const action of [service.applySuggestion, service.verifyIssue, service.saveExperience]) {
        expect(action(id)).toMatchObject({ ok: false, message: expect.stringContaining('暂未接入') });
        expect(action(id).ok).toBe(false);
      }
    }
    expect(service.updateSettings({ enabled: true, webhook: 'https://example.invalid/hook' }).ok).toBe(false);
    expect(service.saveSettings().ok).toBe(false);
    expect(service.getSnapshot()).toBe(original);
    expect(gateway.listWorkflows).not.toHaveBeenCalled();
    expect(gateway.listFlowRuns).not.toHaveBeenCalled();
    expect(gateway.getFlowRun).not.toHaveBeenCalled();
  });

  it('keeps live snapshots frozen and instances isolated; all period metrics remain unavailable', async () => {
    const first = createTaskEscortWorkspaceService('demo', api());
    const second = createTaskEscortWorkspaceService('demo', api());
    const snapshot = first.getSnapshot();
    expect(first.getSnapshot()).toBe(snapshot);
    expect(Object.isFrozen(snapshot)).toBe(true);
    expect(Object.isFrozen(snapshot.settings)).toBe(true);
    expect(() => snapshot.runs.push({} as never)).toThrow();
    await first.load();
    expect(first.getSnapshot().workflows).toHaveLength(1);
    expect(second.getSnapshot().workflows).toEqual([]);
    for (const period of ['today', 'yesterday', '7d', '30d'] as const) {
      expect(first.getMetrics(period)).toMatchObject({
        available: false,
        scope: 'unavailable',
        series: [],
        labels: [],
      });
      expect(first.getMetrics(period)).toBe(first.getMetrics(period));
      expect(Object.isFrozen(first.getMetrics(period).series)).toBe(true);
    }
  });

  it('notifies subscribers for real reads; resetting only clears local data and never restores synthetic state', async () => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService('demo', gateway);
    const subscriber = jest.fn();
    const unsubscribe = service.subscribe(subscriber);
    await service.load();
    expect(subscriber).toHaveBeenCalled();
    expect(service.getSnapshot().workflows).toHaveLength(1);
    unsubscribe();
    const calls = subscriber.mock.calls.length;
    service.reset();
    expect(subscriber).toHaveBeenCalledTimes(calls);
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      issues: [],
      experiences: [],
      settingsDirty: false,
      loading: false,
      error: null,
    });
    expect(service.getSnapshot().settings.enabled).toBe(false);
    expect(gateway.listWorkflows).toHaveBeenCalledTimes(1);
    expect(gateway.listFlowRuns).toHaveBeenCalledTimes(1);
  });

  it('reads real detail for legacy demo callers, never inferring missing runs or nodes', async () => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService('demo', gateway);
    gateway.getFlowRun.mockResolvedValue({ run: rawRun, nodes: [{ node_id: 'real-node', status: 'running' }] });
    await expect(service.getRunDetail('run-real')).resolves.toMatchObject({
      run: { id: 'run-real', status: 'success' },
      nodes: [{ id: 'real-node', status: 'running' }],
    });
    expect(gateway.getFlowRun).toHaveBeenCalledWith('run-real');
    gateway.getFlowRun.mockRejectedValue(new Error('404 Not found'));
    await expect(service.getRunDetail('missing')).rejects.toThrow('404 Not found');
  });

  it.each([
    { label: 'empty', workflows: [], runs: [], expected: { workflows: [], runs: [], error: null } },
    {
      label: 'forbidden',
      workflows: [],
      runs: new Error('403 Forbidden'),
      expected: { workflows: [], runs: [], error: '403 Forbidden' },
    },
    {
      label: 'malformed',
      workflows: null,
      runs: [],
      expected: { workflows: [], runs: [], error: expect.stringContaining('数据结构') },
    },
    {
      label: 'unknown status',
      workflows: [{ workflowId: 'wf-real' }],
      runs: [{ ...rawRun, status: 'future-state' }],
      expected: { workflows: [{ id: 'wf-real' }], runs: [{ id: 'run-real', status: 'unknown' }], error: null },
    },
  ])(
    'does not fall back to fixtures for a legacy demo request with $label data',
    async ({ workflows, runs, expected }) => {
      const gateway = api();
      gateway.listWorkflows.mockResolvedValue(workflows as never);
      if (runs instanceof Error) gateway.listFlowRuns.mockRejectedValue(runs);
      else gateway.listFlowRuns.mockResolvedValue(runs as never);
      const service = createTaskEscortWorkspaceService('demo', gateway);
      await service.load();
      expect(gateway.listWorkflows).toHaveBeenCalledTimes(1);
      expect(gateway.listFlowRuns).toHaveBeenCalledTimes(1);
      expect(service.getSnapshot()).toMatchObject({ issues: [], experiences: [], loading: false, ...expected });
    },
  );
});

describe('Task Escort Workspace live service', () => {
  it('defaults to empty live and exposes unsupported pages instead of demo fallback', async () => {
    const gateway = api();
    gateway.listFlowRuns.mockResolvedValue([rawRun]);
    const service = createTaskEscortWorkspaceService(undefined, gateway);
    expect(service.mode).toBe('live');
    expect(service.getSnapshot().workflows).toEqual([]);
    expect(service.support.dashboard.available).toBe(true);
    expect(service.support.runs.available).toBe(true);
    for (const page of ['workbench', 'issues', 'experiences', 'editor', 'settings'] as const) {
      expect(service.support[page]).toEqual({ available: false, reason: expect.stringContaining('暂未接入') });
    }
    const pending = service.load();
    expect(service.getSnapshot().loading).toBe(true);
    await pending;
    expect(gateway.listWorkflows).toHaveBeenCalledWith();
    expect(gateway.listFlowRuns).toHaveBeenCalledWith({ limit: 100 });
    expect(service.getSnapshot()).toMatchObject({ loading: false, error: null, issues: [], experiences: [] });
    expect(service.getSnapshot().workflows[0]).toMatchObject({
      id: 'wf-real',
      runCount: 1,
      sampleCount: 1,
      status: 'unknown',
    });
    expect(service.getSnapshot().runs[0]).toMatchObject({
      id: 'run-real',
      status: 'success',
      duration: 1.25,
      succeededCount: 2,
      failedCount: 0,
      totalTokens: 75,
      trigger: 'schedule',
      sessionId: 'session-real',
    });
    expect(service.getMetrics('7d')).toMatchObject({ available: false, scope: 'unavailable', series: [], labels: [] });
  });

  it('maps current raw names first and old DTO aliases second, never turns unknown into zero or success', async () => {
    const gateway = api();
    gateway.listFlowRuns.mockResolvedValue([
      {
        ...rawRun,
        succeeded_nodes: 999,
        failed_nodes: 999,
        duration_ms: 999,
        total_tokens: 999,
        trigger: 'old',
        session_id: 'old',
      },
      {
        flow_id: 'old',
        workflow_id: 'wf-real',
        status: 'failed',
        succeeded_nodes: 0,
        failed_nodes: 1,
        duration_ms: 2000,
        total_tokens: 0,
        trigger: 'manual',
        session_id: 'old-session',
      },
      { flow_id: 'unknown', workflow_id: 'wf-real', status: 'brand-new', started_at: null, duration_ms: null },
    ]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    const [current, old, unknown] = service.getSnapshot().runs;
    expect(current).toMatchObject({
      succeededCount: 2,
      failedCount: 0,
      duration: 1.25,
      totalTokens: 75,
      trigger: 'schedule',
      sessionId: 'session-real',
    });
    expect(old).toMatchObject({
      succeededCount: 0,
      failedCount: 1,
      duration: 2,
      totalTokens: 0,
      trigger: 'manual',
      sessionId: 'old-session',
    });
    expect(unknown).toMatchObject({
      status: 'unknown',
      duration: null,
      startedAt: null,
      succeededCount: null,
      failedCount: null,
      totalTokens: null,
      sessionId: null,
    });
    expect(service.getSnapshot().workflows[0].successRate).toBeNull();
    expect(Object.keys(unknown).some((key) => key.includes('_'))).toBe(false);
  });

  it('retains the real load error with empty arrays, and retry clears it without fallback', async () => {
    const gateway = api();
    gateway.listFlowRuns.mockRejectedValue(new Error('403 Forbidden'));
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      issues: [],
      experiences: [],
      loading: false,
      error: '403 Forbidden',
    });
    gateway.listFlowRuns.mockResolvedValue([]);
    await service.load();
    expect(service.getSnapshot().error).toBeNull();
    expect(service.getSnapshot().workflows).toHaveLength(1);
  });

  it.each([
    [new Error(''), '工作流与运行样本加载失败'],
    ['Gateway unavailable', 'Gateway unavailable'],
    ['', '工作流与运行样本加载失败'],
    [null, '工作流与运行样本加载失败'],
    [{ status: 403 }, '工作流与运行样本加载失败'],
  ])('preserves safe error handling for a nonstandard rejection: %p', async (error, expected) => {
    const gateway = api();
    gateway.listFlowRuns.mockRejectedValue(error);
    const service = createTaskEscortWorkspaceService('demo', gateway);
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      issues: [],
      experiences: [],
      loading: false,
      error: expected,
    });
  });

  it('rejects every live write without modifying state or sending a request', () => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService('live', gateway);
    const original = service.getSnapshot();
    for (const result of [
      service.applySuggestion('x'),
      service.verifyIssue('x'),
      service.saveExperience('x'),
      service.updateSettings({ enabled: true }),
      service.saveSettings(),
    ]) {
      expect(result).toMatchObject({ ok: false, message: expect.stringContaining('暂未接入') });
    }
    expect(service.getSnapshot()).toBe(original);
    expect(gateway.listWorkflows).not.toHaveBeenCalled();
    expect(gateway.listFlowRuns).not.toHaveBeenCalled();
    expect(gateway.getFlowRun).not.toHaveBeenCalled();
  });

  it('returns actual node executions only, preserving order, errors and nullable values', async () => {
    const gateway = api();
    gateway.getFlowRun.mockResolvedValue({
      run: rawRun,
      nodes: [
        {
          node_id: 'actual-1',
          node_title: 'Actual tool',
          status: 'failed',
          duration_ms: 1550,
          error_text: 'tool failed',
          input_json: '{"x":1}',
          output_json: null,
        },
        {
          node_id: 'actual-2',
          node_title: null,
          status: 'future-state',
          duration_ms: null,
          error_text: null,
          input_json: null,
          output_json: '',
        },
      ],
    });
    const service = createTaskEscortWorkspaceService('live', gateway);
    const detail = await service.getRunDetail('run-real');
    expect(gateway.getFlowRun).toHaveBeenCalledWith('run-real');
    expect(detail.nodes).toEqual([
      {
        id: 'actual-1',
        title: 'Actual tool',
        status: 'failed',
        duration: 1.55,
        error: 'tool failed',
        input: '{"x":1}',
        output: null,
      },
      { id: 'actual-2', title: 'actual-2', status: 'unknown', duration: null, error: null, input: null, output: '' },
    ]);
    expect(Object.isFrozen(detail.nodes[0])).toBe(true);
    expect(Object.keys(detail.run).some((key) => key.includes('_'))).toBe(false);
  });

  it('propagates detail failures rather than fabricating a run or nodes', async () => {
    const gateway = api();
    gateway.getFlowRun.mockRejectedValue(new Error('404 Not found'));
    await expect(createTaskEscortWorkspaceService('live', gateway).getRunDetail('missing')).rejects.toThrow(
      '404 Not found',
    );
  });

  it('cancels pending loads and details, then permits a later load', async () => {
    const gateway = api();
    const list = deferred<Awaited<ReturnType<TaskEscortWorkspaceApi['listFlowRuns']>>>();
    const detail = deferred<Awaited<ReturnType<TaskEscortWorkspaceApi['getFlowRun']>>>();
    gateway.listFlowRuns.mockReturnValueOnce(list.promise);
    gateway.getFlowRun.mockReturnValueOnce(detail.promise);
    const service = createTaskEscortWorkspaceService('live', gateway);
    const loading = service.load();
    const reading = service.getRunDetail('run-real');
    const rejected = reading.catch((error: unknown) => error);
    service.cancelPending();
    expect(service.getSnapshot().loading).toBe(false);
    list.resolve([rawRun]);
    detail.resolve({ run: rawRun, nodes: [] });
    await loading;
    expect(await rejected).toMatchObject({ message: expect.stringMatching(/cancel|取消/i) });
    expect(service.getSnapshot().runs).toEqual([]);
    await service.load();
    expect(service.getSnapshot().workflows).toHaveLength(1);
  });

  it('ignores older load completions and reset invalidates outstanding work', async () => {
    const gateway = api();
    const stale = deferred<Awaited<ReturnType<TaskEscortWorkspaceApi['listFlowRuns']>>>();
    gateway.listFlowRuns.mockReturnValueOnce(stale.promise).mockResolvedValueOnce([rawRun]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    const old = service.load();
    await service.load();
    stale.reject(new Error('old failure'));
    await old;
    expect(service.getSnapshot().runs).toHaveLength(1);
    expect(service.getSnapshot().error).toBeNull();
    const pending = deferred<Awaited<ReturnType<TaskEscortWorkspaceApi['listFlowRuns']>>>();
    gateway.listFlowRuns.mockReturnValueOnce(pending.promise);
    const resetLoad = service.load();
    service.reset();
    pending.resolve([rawRun]);
    await resetLoad;
    expect(service.getSnapshot()).toMatchObject({ runs: [], workflows: [], loading: false, error: null });
  });
});

describe('Task Escort Workspace boundary regressions', () => {
  it.each(['balanced', 'high', 'low'])(
    'keeps %s sensitivity unavailable rather than simulating a saved setting',
    (sensitivity) => {
      const service = createTaskEscortWorkspaceService('demo', api());
      const original = service.getSnapshot();
      expect(service.updateSettings({ sensitivity }).ok).toBe(false);
      expect(service.saveSettings().ok).toBe(false);
      expect(service.getSnapshot()).toBe(original);
    },
  );

  it('does not label a run creator as a Bot without a Bot identity contract', async () => {
    const gateway = api();
    gateway.listFlowRuns.mockResolvedValue([{ ...rawRun, created_by: 'a-user-not-a-bot' }]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot().runs[0].bot).toBe('未知');
  });

  it.each([
    ['succeeded', 'success'],
    ['success', 'success'],
    ['failed', 'failed'],
    ['running', 'running'],
    ['postActionsRunning', 'running'],
    ['pending', 'waiting'],
    ['waiting', 'waiting'],
    ['cancelled', 'cancelled'],
    ['canceled', 'cancelled'],
    ['skipped', 'unknown'],
    ['blocked', 'unknown'],
    [null, 'unknown'],
  ])('maps backend status %s to safe domain status %s', async (raw, expected) => {
    const gateway = api();
    gateway.listFlowRuns.mockResolvedValue([{ ...rawRun, status: raw }]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot().runs[0].status).toBe(expected);
  });

  it('retains null for invalid numeric/timestamp values and allows real zero', async () => {
    const gateway = api();
    gateway.listFlowRuns.mockResolvedValue([
      {
        ...rawRun,
        total_duration_ms: -10,
        succeeded_count: NaN,
        failed_count: -1,
        total_token_usage: Infinity,
        started_at: 'bad-date',
      },
      { ...rawRun, flow_id: 'zero', total_duration_ms: 0, started_at: 0 },
    ]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    const [invalid, zero] = service.getSnapshot().runs;
    expect(invalid).toMatchObject({
      duration: null,
      succeededCount: null,
      failedCount: null,
      totalTokens: null,
      startedAt: null,
    });
    expect(zero).toMatchObject({ duration: 0, startedAt: '1970-01-01T00:00:00.000Z' });
  });

  it('keeps malformed responses as an error with no synthetic data', async () => {
    const gateway = api();
    gateway.listWorkflows.mockResolvedValue(null as never);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      loading: false,
      error: expect.stringContaining('数据结构'),
    });
  });

  it('rejects malformed detail and missing ids with no inferred topology', async () => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService('live', gateway);
    await expect(service.getRunDetail(' ')).rejects.toThrow('ID');
    gateway.getFlowRun.mockResolvedValue({ run: null } as never);
    await expect(service.getRunDetail('missing')).rejects.toThrow('数据结构');
    gateway.getFlowRun.mockResolvedValue({ run: rawRun });
    await expect(service.getRunDetail('run-real')).resolves.toMatchObject({ nodes: [] });
  });

  it('legacy demo reloads real data and cannot persist local settings or dirty state', async () => {
    const gateway = api();
    const service = createTaskEscortWorkspaceService('demo', gateway);
    const baseline = service.getSnapshot().settings;
    expect(service.updateSettings({ enabled: !baseline.enabled }).ok).toBe(false);
    expect(service.saveSettings().ok).toBe(false);
    await service.load();
    expect(service.getSnapshot().workflows.map((workflow) => workflow.id)).toEqual(['wf-real']);
    expect(service.getSnapshot().settings).toEqual(baseline);
    expect(service.getSnapshot().settingsDirty).toBe(false);
    service.reset();
    expect(service.updateSettings({ enabled: true }).ok).toBe(false);
    expect(service.getSnapshot()).toMatchObject({ workflows: [], runs: [], settingsDirty: false });
  });
});

const invalidIdentityCases = [
  { label: 'missing', value: undefined },
  { label: 'null', value: null },
  { label: 'empty', value: '' },
  { label: 'whitespace-only', value: ' \t\n ' },
  { label: 'number', value: 42 },
  { label: 'boolean', value: false },
  { label: 'object', value: {} },
  { label: 'array', value: [] },
];
const invalidRecords = [null, undefined, 'not-a-record', 42, false, []];
function withInvalidIdentity(base: Record<string, unknown>, field: string, value: unknown) {
  const row = { ...base, [field]: value };
  if (value === undefined) delete row[field];
  return row;
}

// Type assertions here deliberately model malformed JSON that violates the declared API DTOs.
describe('Task Escort Workspace runtime identity validation', () => {
  describe.each(['flow_id', 'workflow_id'])('required run %s', (field) => {
    it.each(invalidIdentityCases)(
      'rejects $label in list reads without keeping partial rows or falling back',
      async ({ value }) => {
        const gateway = api();
        const malformed = withInvalidIdentity(rawRun, field, value);
        gateway.listFlowRuns.mockResolvedValue([rawRun, malformed] as never);
        const service = createTaskEscortWorkspaceService('live', gateway);
        await service.load();
        expect(service.getSnapshot()).toMatchObject({
          workflows: [],
          runs: [],
          issues: [],
          experiences: [],
          loading: false,
          error: expect.stringMatching(new RegExp(`数据结构.*${field}`)),
        });
        expect(service.mode).toBe('live');
      },
    );

    it.each(invalidIdentityCases)(
      'rejects $label in detail reads rather than inventing an identity',
      async ({ value }) => {
        const gateway = api();
        gateway.getFlowRun.mockResolvedValue({ run: withInvalidIdentity(rawRun, field, value), nodes: [] } as never);
        const service = createTaskEscortWorkspaceService('live', gateway);
        await expect(service.getRunDetail('run-real')).rejects.toThrow(new RegExp(`数据结构.*${field}`));
        expect(service.getSnapshot()).toMatchObject({ workflows: [], runs: [], issues: [], experiences: [] });
      },
    );
  });

  it.each(invalidIdentityCases)('rejects a $label workflow identity before exposing workflows', async ({ value }) => {
    const gateway = api();
    gateway.listWorkflows.mockResolvedValue([
      { workflowId: 'valid', title: 'Valid' },
      withInvalidIdentity({ workflowId: 'wf-real', title: 'Malformed' }, 'workflowId', value),
    ] as never);
    gateway.listFlowRuns.mockResolvedValue([rawRun]);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      issues: [],
      experiences: [],
      loading: false,
      error: expect.stringMatching(/数据结构.*workflowId/),
    });
  });

  it.each(invalidIdentityCases)('rejects a $label detail node ID without skipping the node', async ({ value }) => {
    const gateway = api();
    gateway.getFlowRun.mockResolvedValue({
      run: rawRun,
      nodes: [
        { node_id: 'valid-node', status: 'succeeded' },
        withInvalidIdentity({ node_id: 'bad-node' }, 'node_id', value),
      ],
    } as never);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await expect(service.getRunDetail('run-real')).rejects.toThrow(/数据结构.*node_id/);
    expect(service.getSnapshot()).toMatchObject({ workflows: [], runs: [], issues: [], experiences: [] });
  });

  it.each(invalidRecords.map((record) => [record]))(
    'rejects a non-record run row (%p) with a deliberate data error',
    async (row) => {
      const gateway = api();
      gateway.listFlowRuns.mockResolvedValue([row] as never);
      const service = createTaskEscortWorkspaceService('live', gateway);
      await service.load();
      expect(service.getSnapshot()).toMatchObject({
        workflows: [],
        runs: [],
        loading: false,
        error: expect.stringMatching(/数据结构.*运行记录/),
      });
    },
  );

  it.each(invalidRecords.map((record) => [record]))(
    'rejects a non-record workflow row (%p) with a deliberate data error',
    async (row) => {
      const gateway = api();
      gateway.listWorkflows.mockResolvedValue([row] as never);
      const service = createTaskEscortWorkspaceService('live', gateway);
      await service.load();
      expect(service.getSnapshot()).toMatchObject({
        workflows: [],
        runs: [],
        loading: false,
        error: expect.stringMatching(/数据结构.*工作流记录/),
      });
    },
  );

  it.each(invalidRecords.map((record) => [record]))(
    'rejects a non-record detail run (%p) with a deliberate data error',
    async (run) => {
      const gateway = api();
      gateway.getFlowRun.mockResolvedValue({ run, nodes: [] } as never);
      const service = createTaskEscortWorkspaceService('live', gateway);
      await expect(service.getRunDetail('run-real')).rejects.toThrow(/数据结构.*运行记录/);
    },
  );

  it.each(invalidRecords.map((record) => [record]))(
    'rejects a non-record detail node (%p) with a deliberate data error',
    async (node) => {
      const gateway = api();
      gateway.getFlowRun.mockResolvedValue({ run: rawRun, nodes: [{ node_id: 'valid-node' }, node] } as never);
      const service = createTaskEscortWorkspaceService('live', gateway);
      await expect(service.getRunDetail('run-real')).rejects.toThrow(/数据结构.*节点执行记录/);
    },
  );

  it('rejects a malformed run even when no workflow rows were returned', async () => {
    const gateway = api();
    gateway.listWorkflows.mockResolvedValue([]);
    gateway.listFlowRuns.mockResolvedValue([{ workflow_id: 'w1', status: 'running' }] as never);
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      loading: false,
      error: expect.stringMatching(/数据结构.*flow_id/),
    });
  });

  it('accepts valid identities without inventing absent names or changing the original IDs', async () => {
    const gateway = api();
    gateway.listWorkflows.mockResolvedValue([{ workflowId: 'wf-real' }]);
    gateway.listFlowRuns.mockResolvedValue([{ flow_id: 'run-real', workflow_id: 'wf-real' }]);
    gateway.getFlowRun.mockResolvedValue({
      run: { flow_id: 'run-real', workflow_id: 'wf-real' },
      nodes: [{ node_id: 'node-real' }],
    });
    const service = createTaskEscortWorkspaceService('live', gateway);
    await service.load();
    expect(service.getSnapshot().error).toBeNull();
    expect(service.getSnapshot().runs[0]).toMatchObject({ id: 'run-real', workflowId: 'wf-real', name: 'wf-real' });
    await expect(service.getRunDetail('run-real')).resolves.toMatchObject({
      nodes: [{ id: 'node-real', title: 'node-real' }],
    });
  });
});
