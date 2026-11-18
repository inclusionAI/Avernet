import { taskEscortService } from '@/services/taskEscort';
import { createTaskEscortWorkspaceService } from '@/services/taskEscortWorkspace';

jest.mock('@/services/taskEscort', () => ({
  taskEscortService: {
    listWorkflows: jest.fn(),
    listFlowRuns: jest.fn(),
    getFlowRun: jest.fn(),
  },
}));
const gateway = taskEscortService as jest.Mocked<typeof taskEscortService>;

beforeEach(() => jest.clearAllMocks());

describe('Task Escort Workspace existing adapter reuse', () => {
  it.each([undefined, 'demo', 'invalid' as never] as const)(
    'uses only existing real read signatures for requestedMode=%s',
    async (mode) => {
      gateway.listWorkflows.mockResolvedValue([{ workflowId: 'real', title: 'Real', packId: null, updatedAt: 0 }]);
      gateway.listFlowRuns.mockResolvedValue([]);
      gateway.getFlowRun.mockResolvedValue({
        run: {
          flow_id: 'real-run',
          workflow_id: 'real',
          workflow_title: 'Real',
          status: 'succeeded',
          created_by: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
          node_count: 0,
          succeeded_nodes: 0,
          failed_nodes: 0,
          running_nodes: 0,
          total_tokens: null,
          error_message: null,
          session_id: null,
          trigger: null,
        },
        nodes: [],
      });
      const service = createTaskEscortWorkspaceService(mode);
      await service.load();
      const detail = await service.getRunDetail('real-run');
      expect(gateway.listWorkflows).toHaveBeenCalledWith();
      expect(gateway.listFlowRuns).toHaveBeenCalledWith({ limit: 100 });
      expect(gateway.getFlowRun).toHaveBeenCalledWith('real-run');
      expect(detail.run).toMatchObject({ id: 'real-run', status: 'success', duration: null });
      expect(service.getSnapshot().workflows[0].sampleCount).toBe(0);
    },
  );

  it('legacy demo requests fail honestly on default adapter errors and cannot simulate writes', async () => {
    gateway.listWorkflows.mockRejectedValue(new Error('403 Forbidden'));
    gateway.listFlowRuns.mockResolvedValue([]);
    gateway.getFlowRun.mockRejectedValue(new Error('404 Not found'));
    const service = createTaskEscortWorkspaceService('demo');
    await service.load();
    expect(service.getSnapshot()).toMatchObject({
      workflows: [],
      runs: [],
      issues: [],
      experiences: [],
      error: '403 Forbidden',
    });
    await expect(service.getRunDetail('missing')).rejects.toThrow('404 Not found');
    const snapshot = service.getSnapshot();
    expect(service.applySuggestion('i1').ok).toBe(false);
    expect(service.verifyIssue('i1').ok).toBe(false);
    expect(service.saveExperience('i1').ok).toBe(false);
    expect(service.updateSettings({ webhook: 'https://example.invalid/hook' }).ok).toBe(false);
    expect(service.saveSettings().ok).toBe(false);
    expect(service.getSnapshot()).toBe(snapshot);
    service.reset();
    expect(service.getSnapshot()).toMatchObject({ workflows: [], runs: [], issues: [], experiences: [] });
    expect(gateway.listWorkflows).toHaveBeenCalledTimes(1);
    expect(gateway.listFlowRuns).toHaveBeenCalledTimes(1);
    expect(gateway.getFlowRun).toHaveBeenCalledTimes(1);
  });
});
