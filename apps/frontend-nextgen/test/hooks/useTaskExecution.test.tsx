/** @jest-environment jsdom */
import { useTaskExecution } from '@/hooks/useTaskExecution';
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { executeTaskService } from '@/services/tasks/taskService';
import { useTaskStore } from '@/stores/taskStore';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/hooks/useWorkflowList', () => ({
  useWorkflowList: () => ({ workflows: [], workflowsLoading: false, loadWorkflows: jest.fn() }),
}));
jest.mock('@/services/tasks/taskService', () => ({
  executeTaskService: jest.fn(),
  resolveWorkflowCommand: jest.fn(),
}));

const mockedExecute = executeTaskService as unknown as jest.Mock;
const context: TaskComposerContext = {
  sourceType: 'bot',
  ownerUserId: 'user-1',
  ownerBotId: 'bot-main',
  mainSessionId: 'session-1',
};
const form: TaskComposerForm = {
  title: '接力任务',
  instruction: '完成需求实现',
  objective: '交付可运行功能',
  acceptances: ['测试通过'],
  taskType: 'dynamic',
};

function taskRecord(mode?: 'centralized' | 'relay') {
  return {
    task_id: 'task-1',
    task_info: {
      execution_config:
        mode === 'relay'
          ? {
              orchestration_mode: 'relay',
              root_node_id: 'root-1',
              root_execution_instruction:
                '[task-execute]\ntask_id=task-1; node_id=root-1\nrelay_token=generation-1\nbackend_url=https://backend.example',
            }
          : {},
    },
    create_time: '2026-09-17T00:00:00Z',
    finish_time: null,
  };
}

describe('useTaskExecution relay bootstrap', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useTaskStore.getState().reset();
  });

  it('中心化任务继续只发送原副屏消息', async () => {
    mockedExecute.mockResolvedValue(taskRecord('centralized'));
    const submitPanelMessage = jest.fn();
    const submitTaskExecutionMessage = jest.fn();
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage,
        submitTaskExecutionMessage,
      }),
    );

    await act(async () => {
      await result.current.submit(form);
    });

    expect(submitPanelMessage).toHaveBeenCalledWith(expect.stringContaining('<AixUI type="panel"'));
    expect(submitTaskExecutionMessage).not.toHaveBeenCalled();
  });

  it('relay 任务通过专用出口触发当前 holder 执行首棒', async () => {
    mockedExecute.mockResolvedValue(taskRecord('relay'));
    const submitPanelMessage = jest.fn();
    const submitTaskExecutionMessage = jest.fn();
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage,
        submitTaskExecutionMessage,
      }),
    );

    await act(async () => {
      await result.current.submit(form);
    });

    expect(submitTaskExecutionMessage).toHaveBeenCalledWith(
      expect.stringContaining('backend_url=https://backend.example'),
      'bot-main',
    );
    expect(submitPanelMessage).not.toHaveBeenCalled();
  });
});

describe('useTaskExecution selections（多胶囊叠加态）', () => {
  const resolveWorkflowCommand = jest.requireMock('@/services/tasks/taskService').resolveWorkflowCommand as jest.Mock;
  beforeEach(() => {
    jest.clearAllMocks();
    resolveWorkflowCommand.mockResolvedValue({ command: 'cmd-x', title: '工作流X' });
  });

  function renderExecution() {
    return renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage: jest.fn(),
      }),
    );
  }

  it('多工作流可叠加、同一工作流按 workflowId 去重', () => {
    const { result } = renderExecution();
    act(() => {
      result.current.selectWorkflow({ workflowId: 'wf-1', title: '工作流A' });
      result.current.selectWorkflow({ workflowId: 'wf-2', title: '工作流B' });
      result.current.selectWorkflow({ workflowId: 'wf-1', title: '工作流A-重复' });
    });
    expect(result.current.selections).toHaveLength(2);
    expect(result.current.selections.map((s) => (s.kind === 'workflow' ? s.workflow.workflowId : s.kind))).toEqual([
      'wf-1',
      'wf-2',
    ]);
  });

  it('动态任务全场至多一个，可与工作流共存', () => {
    const { result } = renderExecution();
    act(() => {
      result.current.selectDynamic();
      result.current.selectDynamic();
    });
    expect(result.current.selections).toEqual([{ kind: 'dynamic' }]);
    act(() => {
      result.current.selectWorkflow({ workflowId: 'wf-1', title: '工作流A' });
    });
    expect(result.current.selections).toEqual([{ kind: 'dynamic' }, expect.objectContaining({ kind: 'workflow' })]);
  });

  it('removeSelection 按 key 单独清除，clearSelection 全清', () => {
    const { result } = renderExecution();
    act(() => {
      result.current.selectDynamic();
      result.current.selectWorkflow({ workflowId: 'wf-1', title: '工作流A' });
      result.current.removeSelection('wf-1');
    });
    expect(result.current.selections).toEqual([{ kind: 'dynamic' }]);
    act(() => {
      result.current.clearSelection();
    });
    expect(result.current.selections).toEqual([]);
  });
});
