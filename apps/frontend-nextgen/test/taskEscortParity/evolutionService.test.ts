import { evolutionController } from '@/services/backendApi/harnessFlow/evolutionController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { issueGroupView, lessonView } from '@/services/taskEscortParity/evolutionMapper';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const groupFixture = {
  workflowId: 'wf',
  signature: 'sig',
  inputDigest: 'digest',
  flowIds: ['r'],
  sources: [
    {
      sourceId: 's',
      analysisId: 'a',
      diagnosisId: 'd',
      flowId: 'r',
      flowIds: ['r'],
      nodeId: 'n',
      failureMode: 'timeout',
      failureSignature: 'sig',
      reasoning: 'actual reason',
      completedAtMs: 1794902400000,
      evidenceEventIds: ['ev'],
    },
  ],
  summary: null,
  stale: true,
  aggregationStatus: 'not_generated',
  aggregationId: null,
};
beforeEach(() => request.mockReset());
it('uses GET gateway DTOs, exact filters and actual identity/cancellation on every evolution read', async () => {
  request.mockResolvedValue({});
  const signal = new AbortController().signal;
  await evolutionController.groups(
    'wf/a',
    'alice',
    {
      versionScope: 'historical',
      page: 2,
      pageSize: 50,
      sort: 'frequency',
      severity: 'high',
      nodeId: 'n',
      failureMode: 'timeout',
    },
    signal,
  );
  expect(request).toHaveBeenLastCalledWith(
    '/openapi/v1/harnessflow/api/evolve/issue-groups',
    expect.objectContaining({
      params: {
        user_id: 'alice',
        workflowId: 'wf/a',
        versionScope: 'historical',
        page: 2,
        pageSize: 50,
        sort: 'frequency',
        severity: 'high',
        nodeId: 'n',
        failureMode: 'timeout',
      },
      signal,
    }),
  );
  await evolutionController.groups('wf', 'alice', { versionScope: 'all', signature: 'sig' }, signal);
  expect(request.mock.calls.at(-1)[1].params).not.toHaveProperty('page');
  await evolutionController.access('wf/a', 'alice', signal);
  expect(request).toHaveBeenLastCalledWith(
    '/openapi/v1/harnessflow/api/workflows/wf%2Fa/access',
    expect.objectContaining({ params: { user_id: 'alice' } }),
  );
  await evolutionController.analysis('r/a', 'a1', 'alice', signal);
  expect(request).toHaveBeenLastCalledWith(
    '/openapi/v1/harnessflow/api/evolve/runs/r%2Fa/analysis-result',
    expect.objectContaining({ params: { analysisId: 'a1', user_id: 'alice' } }),
  );
  await evolutionController.diagnoses('wf', 'alice', { flowId: 'r', analysisId: 'a', limit: 10, offset: 20 }, signal);
  await evolutionController.suggestions('wf', 'alice', { status: 'failed', limit: 20, offset: 40 }, signal);
  await evolutionController.applyTasks(['a', 'b'], 'alice', signal);
  expect(request.mock.calls.at(-1)[0]).toBe(
    '/openapi/v1/harnessflow/api/evolve/suggestions/apply-tasks?user_id=alice&suggestionIds=a&suggestionIds=b',
  );
  await evolutionController.lessons('wf', 'alice', { status: 'verified', query: 'needle', limit: 100 }, signal);
  expect(
    request.mock.calls.every(
      ([path, o]) =>
        o.method === 'GET' &&
        o.injectUserId === false &&
        (o.params?.user_id ?? new URL(path, 'https://local.invalid').searchParams.get('user_id')) === 'alice' &&
        o.signal === signal,
    ),
  ).toBe(true);
});
it('preserves single original reasoning, unknowns and null lesson values without inferred percentages', () => {
  const group = issueGroupView(groupFixture, 'wf');
  expect(group.sources[0].reasoning).toBe('actual reason');
  expect(group.summary).toBeNull();
  const lesson = lessonView({
    id: 1,
    lesson_id: 'l',
    workflow_id: null,
    node_id: null,
    failure_signature: 's',
    failure_mode: 'x',
    fix_kind: 'alert',
    status: 'draft',
    hit_count: 10,
    rescued_count: 8,
    successRate: null,
    source: null,
    gmt_create: null,
  });
  expect(lesson).toEqual(
    expect.objectContaining({ workflowId: null, nodeId: null, successRate: null, source: null, createdAt: null }),
  );
});
it.each([403, 404, 409, 503])('keeps HTTP %s distinct and never retries latest analysis', async (status) => {
  request.mockRejectedValue(Object.assign(new Error('upstream'), { status }));
  await expect(evolutionService.analysis('wf', 'r', 'missing', 'alice', new AbortController().signal)).rejects.toThrow(
    String(status),
  );
  expect(request).toHaveBeenCalledTimes(1);
});
it('rejects mismatched workflow/analysis and malformed data, not empty success', async () => {
  request.mockResolvedValue({
    groups: [{ ...groupFixture, workflowId: 'other' }],
    facets: { nodes: [], modes: [] },
    page: { page: 1, pageSize: 10, total: 1, totalPages: 1 },
  });
  await expect(
    evolutionService.groups(
      'wf',
      'alice',
      { versionScope: 'current', page: 1, pageSize: 10 },
      new AbortController().signal,
    ),
  ).rejects.toThrow();
  request.mockResolvedValue({ analysis: { workflowId: 'wf', flowId: 'r', analysisId: 'latest' } });
  await expect(evolutionService.analysis('wf', 'r', 'old', 'alice', new AbortController().signal)).rejects.toThrow();
  request.mockResolvedValue({});
  await expect(evolutionService.lessons('wf', 'alice', {}, new AbortController().signal)).rejects.toThrow();
});
