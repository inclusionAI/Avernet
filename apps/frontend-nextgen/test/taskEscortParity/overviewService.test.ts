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
