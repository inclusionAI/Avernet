/** @jest-environment jsdom */
import TaskEscortPage from '@/pages/TaskEscort';
import { backendRequest } from '@/services/backendApi/httpClient';
import { taskEscortService } from '@/services/taskEscort/taskEscortService';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

// Dashboard/shell integration keeps all secondary leaves mocked; each owner tests its real content.
function mockLeaf({ context }: { context: import('@/domain/taskEscortParity/types').TaskEscortParityContext }) {
  return <div>{context.workflowId}</div>;
}
jest.mock('../../src/pages/TaskEscort/parity/overview/OverviewPage', () => ({ OverviewPage: mockLeaf }), {
  virtual: true,
});
jest.mock('../../src/pages/TaskEscort/parity/evolution/IssuePage', () => ({ IssuePage: mockLeaf }), { virtual: true });
jest.mock('../../src/pages/TaskEscort/parity/evolution/ExperiencePage', () => ({ ExperiencePage: mockLeaf }), {
  virtual: true,
});
jest.mock('../../src/pages/TaskEscort/parity/editor/WorkflowEditorPage', () => ({ WorkflowEditorPage: mockLeaf }), {
  virtual: true,
});
jest.mock('../../src/pages/TaskEscort/parity/management/ManagementPage', () => ({ ManagementPage: mockLeaf }), {
  virtual: true,
});

jest.mock('@umijs/max', () => {
  const router = require('history').createMemoryHistory();
  const React = require('react');
  return {
    history: router,
    useLocation: () =>
      React.useSyncExternalStore(
        (notify: () => void) => router.listen(notify),
        () => router.location,
      ),
  };
});
jest.mock('@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceScope', () => ({
  useTaskEscortWorkspaceScope: () => ({ key: 'real-user:real-space', userId: 'real-user', ready: true }),
}));
jest.mock('@/hooks/taskEscortWorkspace/useTaskEscortDashboardAccess', () => ({
  useTaskEscortDashboardAccess: () => 'allowed',
}));
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/services/taskEscort/taskEscortService', () => ({
  taskEscortService: { listWorkflows: jest.fn(), listFlowRuns: jest.fn(), getFlowRun: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));
const request = jest.mocked(backendRequest);
const workspaceApi = jest.mocked(taskEscortService);
const latestOverview = () =>
  request.mock.calls.filter(([url]) => url.endsWith('/dashboard/overview')).at(-1)?.[1]?.params;
const expectDays = async (days: number) =>
  waitFor(() => {
    const params = latestOverview();
    expect((params?.to as number) - (params?.from as number)).toBe(days * 86400);
    expect(Object.keys(params ?? {}).sort()).toEqual(['from', 'to']);
  });
beforeEach(() => {
  jest.clearAllMocks();
  workspaceApi.listWorkflows.mockResolvedValue([]);
  workspaceApi.listFlowRuns.mockResolvedValue([]);
  request.mockImplementation(async (url, options) => {
    if (url.endsWith('/runs/workflow-types'))
      return {
        workflows: [{ workflow_id: 'real-a', workflow_title: '真实工作流 A' }],
        total: 1,
        limit: 500,
        offset: 0,
      } as any;
    if (url.endsWith('/overview')) return { totalRuns: 1234, statusDistribution: {}, successRate: 0.875 } as any;
    if (url.endsWith('/daily-trend')) return { ...options?.params, dates: [] } as any;
    if (url.endsWith('/failure-hotspots')) return { ...options?.params, hotspots: [] } as any;
    return { ...options?.params, available: true, workflows: [] } as any;
  });
});
it('honors a deep-linked 30d period on initial entry and on a simulated reload', async () => {
  history.replace('/evolution-factory/task-escort?view=dashboard&period=30d&workflowId=real-a&demo=1#keep');
  const first = render(<TaskEscortPage />);
  await screen.findByText('1,234');
  expect(screen.getByRole('button', { name: '30天' }).getAttribute('aria-pressed')).toBe('true');
  await expectDays(30);
  expect(new URLSearchParams(history.location.search).get('workflowId')).toBe('real-a');
  expect(history.location.hash).toBe('#keep');
  first.unmount();
  request.mockClear();
  render(<TaskEscortPage />);
  await screen.findByText('1,234');
  await expectDays(30);
});
it('writes the shared period, preserves it away/back, and restores global requests on browser Back/Forward', async () => {
  history.replace('/evolution-factory/task-escort?view=dashboard&period=30d&keep=yes#detail');
  render(<TaskEscortPage />);
  await screen.findByText('1,234');
  await expectDays(30);
  fireEvent.click(screen.getByRole('button', { name: '7天' }));
  await waitFor(() => expect(new URLSearchParams(history.location.search).get('period')).toBe('7d'));
  await expectDays(7);
  act(() => history.back());
  await expectDays(30);
  expect(screen.getByRole('button', { name: '30天' }).getAttribute('aria-pressed')).toBe('true');
  act(() => history.forward());
  await expectDays(7);
  fireEvent.click(screen.getByRole('button', { name: '运行概览' }));
  await screen.findAllByText('real-a');
  expect(new URLSearchParams(history.location.search).get('period')).toBe('7d');
  fireEvent.click(screen.getByRole('button', { name: '数据大盘' }));
  await screen.findByText('1,234');
  await expectDays(7);
  expect(new URLSearchParams(history.location.search).get('keep')).toBe('yes');
  expect(history.location.hash).toBe('#detail');
  expect(workspaceApi.listWorkflows).not.toHaveBeenCalled();
  expect(workspaceApi.listFlowRuns).not.toHaveBeenCalled();
});
