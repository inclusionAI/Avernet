/** @jest-environment jsdom */
import { useTaskEscortWorkspace } from '@/hooks/useTaskEscortWorkspace';
import { taskEscortService } from '@/services/taskEscort/taskEscortService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@umijs/max', () => {
  const history = require('history').createMemoryHistory();
  return { history, useLocation: () => history.location };
});
jest.mock('sonner', () => ({ toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));
jest.mock('@/services/taskEscort/taskEscortService', () => ({
  taskEscortService: {
    listWorkflows: jest.fn(async () => []),
    listFlowRuns: jest.fn(async () => []),
    getFlowRun: jest.fn(),
  },
}));
const api = jest.mocked(taskEscortService);
beforeEach(() => jest.clearAllMocks());

it('formal loadWorkspace=false suppresses both automatic and explicit generic 100-run sample loads', async () => {
  const { result, rerender } = renderHook(({ enabled }) => useTaskEscortWorkspace('a', { loadWorkspace: enabled }), {
    initialProps: { enabled: false },
  });
  await act(async () => result.current.refresh());
  expect(api.listWorkflows).not.toHaveBeenCalled();
  expect(api.listFlowRuns).not.toHaveBeenCalled();
  rerender({ enabled: true });
  await waitFor(() => expect(api.listFlowRuns).toHaveBeenCalledWith({ limit: 100 }));
  rerender({ enabled: false });
  await act(async () => result.current.refresh());
  expect(api.listFlowRuns).toHaveBeenCalledTimes(1);
});

it('defaults to the existing generic behavior for isolated legacy consumers', async () => {
  renderHook(() => useTaskEscortWorkspace('legacy'));
  await waitFor(() => expect(api.listFlowRuns).toHaveBeenCalledWith({ limit: 100 }));
});
