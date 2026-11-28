/** @jest-environment jsdom */

import { useTaskEscortFlowConfig } from '@/hooks/useTaskEscortFlowConfig';
import { taskEscortService } from '@/services/taskEscort';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/taskEscort', () => ({
  taskEscortService: {
    createWorkflowFromYaml: jest.fn(),
    getWorkflow: jest.fn(),
    listWorkflows: jest.fn(),
    saveWorkflowSpec: jest.fn(),
  },
  WorkflowImportValidationError: class WorkflowImportValidationError extends Error {},
}));

const mockedService = taskEscortService as jest.Mocked<typeof taskEscortService>;

describe('useTaskEscortFlowConfig', () => {
  beforeEach(() => {
    mockedService.listWorkflows.mockReset();
    mockedService.getWorkflow.mockReset();
    mockedService.createWorkflowFromYaml.mockReset();
    mockedService.saveWorkflowSpec.mockReset();
    mockedService.listWorkflows.mockResolvedValue([]);
  });

  test('selects the newly created workflow and refreshes the list', async () => {
    const created = { id: 'risk-processor', version: '1', title: '风险处理器', nodes: [] };
    mockedService.createWorkflowFromYaml.mockResolvedValue(created);
    mockedService.listWorkflows
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ workflowId: 'risk-processor', title: '风险处理器', packId: null, updatedAt: 1 }]);

    const { result } = renderHook(() =>
      useTaskEscortFlowConfig({ botOwnerId: 'space-1', botId: 'bot-1', enabled: true }),
    );
    await waitFor(() => expect(mockedService.listWorkflows).toHaveBeenCalledTimes(1));

    let createResult: Awaited<ReturnType<typeof result.current.createWorkflowFromYaml>> | undefined;
    await act(async () => {
      createResult = await result.current.createWorkflowFromYaml({ yaml: 'id: risk-processor' });
    });

    expect(mockedService.createWorkflowFromYaml).toHaveBeenCalledWith(
      { yaml: 'id: risk-processor', botOwnerId: 'space-1', botId: 'bot-1' },
      [],
    );
    expect(createResult).toEqual({ ok: true });
    expect(result.current.selectedWorkflowId).toBe('risk-processor');
    expect(result.current.spec).toEqual(created);
    expect(result.current.workflows).toHaveLength(1);
  });

  test('tracks unsaved spec changes and clears dirty after a successful save', async () => {
    const workflow = { id: 'risk-processor', version: '1', title: '风险处理器', nodes: [] };
    mockedService.getWorkflow.mockResolvedValue(workflow);
    mockedService.saveWorkflowSpec.mockResolvedValue(undefined as never);

    const { result } = renderHook(() =>
      useTaskEscortFlowConfig({ botOwnerId: 'space-1', botId: 'bot-1', enabled: true }),
    );
    await act(async () => result.current.selectWorkflow('risk-processor'));
    await waitFor(() => expect(result.current.spec).toEqual(workflow));
    expect(result.current.dirty).toBe(false);

    act(() => result.current.updateSpec({ ...workflow, title: '风险处理器 v2' }));
    expect(result.current.dirty).toBe(true);

    await act(async () => {
      await result.current.saveSpec();
    });
    expect(result.current.dirty).toBe(false);
  });
});
