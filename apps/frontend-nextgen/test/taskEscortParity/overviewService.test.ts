import { overviewController } from '@/services/backendApi/harnessFlow/overviewController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { createOverviewService } from '@/services/taskEscortParity/overviewService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const run = { flow_id: 'r1', workflow_id: 'wf', status: 'succeeded', started_at: 1794902400, node_count: 1 };
const node = {
  flow_id: 'r1',
  node_id: 'n1',
  status: 'failed',
  attempt: 2,
  duration_ms: 1050,
  started_at: '1794902400',
  input_json: '{"real":true}',
  output_json: null,
  error_text: 'timeout',
  token_usage_json: '{"totalTokens":8}',
  system_context_json: '{"cwd":"/actual"}',
};
beforeEach(() => request.mockReset());

describe('overview protocol and validation', () => {
  it('routes every verified read through the existing gateway and bound identity with cancellation', async () => {
    request.mockResolvedValue({});
    const signal = new AbortController().signal;
    await overviewController.runs(
      {
        workflowId: 'wf/a',
        user_id: 'alice',
        limit: 20,
        offset: 20,
        statuses: 'cancelled,canceled',
        query: 'hello',
        from: 12,
        to: 34,
      },
      signal,
    );
    expect(request).toHaveBeenLastCalledWith(
      '/openapi/v1/harnessflow/api/runs',
      expect.objectContaining({
        method: 'GET',
        signal,
        injectUserId: false,
        params: expect.objectContaining({ user_id: 'alice', limit: 20, offset: 20, statuses: 'cancelled,canceled' }),
      }),
    );
    for (const [method, suffix] of [
      ['health', 'health'],
      ['nodeStats', 'node-stats'],
      ['trend', 'success-trend'],
    ] as const) {
      await overviewController[method]('wf/a', { user_id: 'alice', days: 7 }, signal);
      expect(request).toHaveBeenLastCalledWith(
        `/openapi/v1/harnessflow/api/workflows/wf%2Fa/${suffix}`,
        expect.objectContaining({ method: 'GET', params: { user_id: 'alice', days: 7 }, signal }),
      );
    }
    await overviewController.nodes('r/a', { user_id: 'alice' }, signal);
    expect(request).toHaveBeenLastCalledWith(
      '/openapi/v1/harnessflow/api/runs/r%2Fa/nodes',
      expect.objectContaining({ params: { user_id: 'alice', full: true } }),
    );
    await overviewController.timeline('r/a', { user_id: 'alice' }, signal);
    expect(request).toHaveBeenLastCalledWith(
      '/openapi/v1/harnessflow/api/run-archives/r%2Fa/timeline',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(request.mock.calls.every(([, options]) => options.method === 'GET' && options.injectUserId === false)).toBe(
      true,
    );
  });
  it('keeps missing/malformed optional metrics unknown, never falls back to run sample counts', async () => {
    const service = createOverviewService();
    request.mockResolvedValueOnce({ runs: [run], total: 128 });
    expect((await service.runs({ workflowId: 'wf', user_id: 'alice', limit: 1 })).statusCounts).toBeNull();
    request.mockResolvedValueOnce({ runs: [run], total: 128, statusCounts: { succeeded: 110, failed: 18 } });
    expect((await service.runs({ workflowId: 'wf', limit: 1 })).statusCounts).toEqual({ succeeded: 110, failed: 18 });
    request.mockResolvedValueOnce({ runs: [run], total: 128, statusCounts: { succeeded: 'bad' } });
    expect((await service.runs({ workflowId: 'wf' })).statusCounts).toBeNull();
    request.mockResolvedValueOnce({ data: { overallScore: 'bad', p95DurationMs: null } });
    expect(await service.health('wf', { days: 7 })).toEqual(
      expect.objectContaining({ overallScore: null, p95DurationMs: null }),
    );
  });
  it('rejects malformed lists and wrong workflow rows rather than silently substituting zero', async () => {
    const service = createOverviewService();
    for (const response of [
      { runs: null, total: 1 },
      { runs: [run] },
      { runs: [{ ...run, workflow_id: 'other' }], total: 1 },
    ]) {
      request.mockResolvedValueOnce(response);
      await expect(service.runs({ workflowId: 'wf' })).rejects.toThrow();
    }
    request.mockRejectedValueOnce(Object.assign(new Error('403 无权限'), { status: 403 }));
    await expect(service.runs({ workflowId: 'wf' })).rejects.toThrow('403');
  });
  it('maps real run and all node attempts, denies cross-workflow or cross-run evidence', async () => {
    const service = createOverviewService();
    request.mockResolvedValueOnce({ run, nodes: [node] }).mockResolvedValueOnce([node]);
    const detail = await service.runDetail('wf', 'r1', { user_id: 'alice' });
    expect(detail.run.startedAt).toBe(Date.parse('2026-11-17T08:00:00Z'));
    expect(detail.nodes[0]).toEqual(
      expect.objectContaining({ nodeId: 'n1', attempt: 2, durationMs: 1050, error: 'timeout', input: '{"real":true}' }),
    );
    request.mockResolvedValueOnce({ run: { ...run, workflow_id: 'other' }, nodes: [] });
    await expect(service.runDetail('wf', 'r1', {})).rejects.toThrow(/工作流/);
    request.mockResolvedValueOnce({ run, nodes: [] }).mockResolvedValueOnce([{ ...node, flow_id: 'other' }]);
    await expect(service.runDetail('wf', 'r1', {})).rejects.toThrow(/运行/);
  });
  it('uses exact trend DTO and preserves nullable values rather than inventing dates or zeroes', async () => {
    request.mockResolvedValueOnce({
      data: [{ date: '2026-11-17', totalRuns: 20, succeededRuns: 16, failedRuns: 4, successRate: 80 }],
    });
    expect(await createOverviewService().trend('wf', { days: 7 })).toEqual([
      { date: '2026-11-17', totalRuns: 20, succeededRuns: 16, failedRuns: 4, successRate: 80 },
    ]);
    request.mockResolvedValueOnce({ data: [{ date: '2026-11-17', totalRuns: 3, successRate: 'oops' }] });
    expect((await createOverviewService().trend('wf', { days: 7 }))[0].successRate).toBeNull();
  });
  it('logs contain only actual run_log events; archive identity must match', async () => {
    const service = createOverviewService();
    request.mockResolvedValueOnce({
      data: {
        flowId: 'r1',
        events: [
          {
            id: 'real',
            source: 'run_log',
            timestamp: 1794902400,
            nodeId: 'n1',
            displayType: 'warn',
            detail: 'real log',
            payload: { level: 'warn', source: 'runner' },
          },
          { id: 'synthetic', source: 'synthetic', title: 'fake inferred event' },
        ],
      },
    });
    expect(await service.logs('r1', {})).toEqual([
      expect.objectContaining({ id: 'real', message: 'real log', level: 'warn' }),
    ]);
    request.mockResolvedValueOnce({ data: { archive: { flowId: 'other' } } });
    await expect(service.archive('wf', 'r1', {})).rejects.toThrow(/运行/);
  });
});

it('maps detail-only verified launch/lineage/projection fields with display redaction before domain state', async () => {
  const service = createOverviewService();
  request
    .mockResolvedValueOnce({
      run: {
        ...run,
        input_json:
          '{"message":"token=unsafe-input","command":"/stored","params":{"late":"never-fallback"},"iamToken":"unsafe"}',
        params_json: '{"target":"original","nested":[{"iamToken":"unsafe-param","system_context":{"unsafe":true}}]}',
        parent_flow_id: 'parent',
        root_flow_id: 'root',
        device_id: ' device ',
        bot_type: 'public',
        stage: 'verify',
        credentials_json: '{"CLIENT_ID":"never-parse"}',
        state_json: 'never-retain',
        arbitrary: 'never-retain',
        hasCredentials: true,
      },
      nodes: [],
    })
    .mockResolvedValueOnce([
      { ...node, system_context_json: 'never-retain', input_json: '{"api_key":"unsafe-node","business":0}' },
    ]);
  const detail = await service.runDetail('wf', 'r1', { user_id: 'alice' });
  expect(detail.run).toEqual(
    expect.objectContaining({
      parentRunId: 'parent',
      rootRunId: 'root',
      deviceId: ' device ',
      botType: 'public',
      stage: 'verify',
      launchInput: expect.objectContaining({ text: '[包含敏感配置的文本已隐藏]' }),
      launchParams: expect.objectContaining({ text: expect.stringContaining('original') }),
    }),
  );
  const serialized = JSON.stringify(detail);
  expect(serialized).not.toMatch(/unsafe|never-retain|never-parse|never-fallback/);
  expect(detail.nodes[0].context).toBeNull();
  expect(detail.nodes[0].input).toContain('[已隐藏]');
  request.mockResolvedValueOnce({
    runs: [{ ...run, input_json: '"not-list-data"', device_id: 'detail-only' }],
    total: 1,
  });
  const list = await service.runs({ workflowId: 'wf' });
  expect(list.runs[0]).not.toHaveProperty('launchInput');
  expect(list.runs[0]).not.toHaveProperty('deviceId');
  expect(
    request.mock.calls.every(
      ([, options]) => options.method === 'GET' && !options.headers && !('exposeRuntimeMetadata' in options.params),
    ),
  ).toBe(true);
});

it('redacts run evidence before retaining node token objects, log bodies and archive runtime sections', async () => {
  const service = createOverviewService();
  request
    .mockResolvedValueOnce({ run, nodes: [] })
    .mockResolvedValueOnce([{ ...node, token_usage_json: '{"totalTokens":8,"iamToken":"node-token-secret"}' }]);
  const detail = await service.runDetail('wf', 'r1', {});
  expect(detail.nodes[0].tokens).toContain('8');
  expect(detail.nodes[0].tokens).not.toContain('node-token-secret');
  request.mockResolvedValueOnce({
    data: {
      flowId: 'r1',
      events: [{ id: 'log', source: 'run_log', detail: '{"business":"retained","iamToken":"log-token-secret"}' }],
    },
  });
  expect(JSON.stringify(await service.logs('r1', {}))).not.toContain('log-token-secret');
  request.mockResolvedValueOnce({
    data: {
      archive: { flowId: 'r1' },
      flowRun: {
        flow_id: 'r1',
        workflow_id: 'wf',
        credentials_json: 'archive-credential-secret',
        state_json: 'internal-state-value',
      },
      nodeExecutions: [
        {
          system_context_json: 'internal-system-value',
          output_json: '{"result":"real","apiKey":"nested-output-secret"}',
        },
      ],
      analysis: {
        source: 'archive',
        status: 'completed',
        diagnoses: [{ iamToken: 'diagnostic-secret', evidence: 'actual diagnosis' }],
      },
    },
  });
  const archive = await service.archive('wf', 'r1', {});
  expect(JSON.stringify(archive)).not.toMatch(
    /archive-credential-secret|internal-state-value|internal-system-value|nested-output-secret|diagnostic-secret/,
  );
  expect(JSON.stringify(archive)).toContain('actual diagnosis');
});

it('allows numeric token metrics, not credential strings disguised as token metrics', async () => {
  const service = createOverviewService();
  request.mockResolvedValueOnce({ run, nodes: [] }).mockResolvedValueOnce([
    {
      ...node,
      token_usage_json:
        '{"totalTokens":"disguised-credential-value","inputTokens":3,"outputTokens":5,"nested":"{\\"\\u0069amToken\\":\\"escaped-credential-value\\"}"}',
      output_json: '{"business":"actual-output","credentials_json":"output-credential-value"}',
      resolved_prompt: 'authorization=prompt-credential-value',
      progress_message: 'system_context=progress-credential-value',
      credentials_json: 'unmapped-credential-value',
      state_json: 'unmapped-state-value',
    },
  ]);
  const detail = await service.runDetail('wf', 'r1', {});
  const serialized = JSON.stringify(detail);
  expect(serialized).not.toMatch(
    /disguised-credential-value|escaped-credential-value|output-credential-value|prompt-credential-value|progress-credential-value|unmapped-credential-value|unmapped-state-value/,
  );
  expect(detail.nodes[0].tokens).toContain('"inputTokens":3');
  expect(detail.nodes[0].tokens).toContain('"outputTokens":5');
  expect(detail.nodes[0].output).toContain('actual-output');
  for (const key of ['credentials_json', 'state_json', 'hasCredentials', 'arbitrary'])
    expect(detail.run).not.toHaveProperty(key);
});
