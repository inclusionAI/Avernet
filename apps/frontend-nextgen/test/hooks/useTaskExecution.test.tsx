/** @jest-environment jsdom */
import { useTaskExecution } from '@/hooks/useTaskExecution';
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { executeTaskService } from '@/services/tasks/taskService';
import { useTaskStore } from '@/stores/taskStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useWorkflowList', () => ({
  useWorkflowList: () => ({ workflows: [], workflowsLoading: false, loadWorkflows: jest.fn() }),
}));
jest.mock('@/services/tasks/taskService', () => ({
  executeTaskService: jest.fn(),
  resolveWorkflowCommand: jest.fn(),
}));
jest.mock('@/services/workspace/botSkillService', () => ({
  botSkillService: {
    listSkillsForBotId: jest.fn(),
    hasTaskIntentSkill: jest.fn(),
    // useTaskExecution 现走 hasTaskIntentSkillForBot:属主 bot → 列技能;非属主 → match 探针。
    hasTaskIntentSkillForBot: jest.fn(),
  },
}));

const mockedExecute = executeTaskService as unknown as jest.Mock;
const mockedHasTaskIntent = jest.requireMock('@/services/workspace/botSkillService').botSkillService
  .hasTaskIntentSkillForBot as jest.Mock;
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
              root_execution_instruction: [
                '[task-execute]',
                'task_id=task-1; node_id=root-1',
                'relay_token=x',
                'backend_url=https://backend.example',
              ].join('\n'),
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
    mockedExecute.mockResolvedValue(taskRecord('centralized'));
    mockedHasTaskIntent.mockResolvedValue({ ok: true, data: false });
    useTaskStore.getState().reset();
  });

  it('无任务上下文时不请求 Bot skills', async () => {
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context: null,
        submitPanelMessage: jest.fn(),
      }),
    );

    await waitFor(() => expect(result.current.dynamicTaskChecking).toBe(false));
    expect(result.current.dynamicTaskEnabled).toBe(false);
    expect(mockedHasTaskIntent).not.toHaveBeenCalled();
  });

  it('安装 task-intake 后动态任务可选', async () => {
    mockedHasTaskIntent.mockResolvedValue({ ok: true, data: true });
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage: jest.fn(),
      }),
    );

    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));
    expect(mockedHasTaskIntent).toHaveBeenCalledWith({
      realBotId: 'bot-main',
      botUuid: 'bot-main',
      ownerId: undefined,
      currentUserId: 'user-1',
    });
    act(() => result.current.selectDynamic());
    expect(result.current.selections).toEqual([{ kind: 'dynamic' }]);
  });

  it('任务探针传入完整 bot_uuid,同时拆分 realBotId/ownerId', async () => {
    const scopedContext: TaskComposerContext = {
      ...context,
      ownerBotId: 'bot-main:owner-user',
    };
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context: scopedContext,
        submitPanelMessage: jest.fn(),
      }),
    );

    await waitFor(() => expect(result.current.dynamicTaskChecking).toBe(false));
    expect(mockedHasTaskIntent).toHaveBeenCalledWith({
      realBotId: 'bot-main',
      botUuid: 'bot-main:owner-user',
      ownerId: 'owner-user',
      currentUserId: 'user-1',
    });
  });

  it('同一个 Driver Bot 切换会话复用 Skill 结果,避免重复请求', async () => {
    const { result, rerender } = renderHook(
      ({ ctx }) =>
        useTaskExecution({
          panelRef: { current: null } as never,
          context: ctx,
          submitPanelMessage: jest.fn(),
        }),
      { initialProps: { ctx: context } },
    );

    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(false));
    expect(mockedHasTaskIntent).toHaveBeenCalledTimes(1);

    rerender({ ctx: { ...context, mainSessionName: 'session-switched' } });
    await waitFor(() => expect(result.current.dynamicTaskChecking).toBe(false));
    expect(mockedHasTaskIntent).toHaveBeenCalledTimes(1);
    expect(result.current.dynamicTaskEnabled).toBe(false);
  });

  it('切换 Driver Bot 后重新探测并更新入口状态', async () => {
    mockedHasTaskIntent
      .mockResolvedValueOnce({ ok: true, data: true })
      .mockResolvedValueOnce({ ok: true, data: false });
    const botA: TaskComposerContext = { ...context, ownerBotId: 'bot-a:user-1' };
    const botB: TaskComposerContext = { ...context, ownerBotId: 'bot-b:user-1' };
    const { result, rerender } = renderHook(
      ({ ctx }) =>
        useTaskExecution({
          panelRef: { current: null } as never,
          context: ctx,
          submitPanelMessage: jest.fn(),
        }),
      { initialProps: { ctx: botA } },
    );

    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));
    rerender({ ctx: botB });
    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(false));
    rerender({ ctx: botA });
    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));

    expect(mockedHasTaskIntent).toHaveBeenNthCalledWith(1, {
      realBotId: 'bot-a',
      botUuid: 'bot-a:user-1',
      ownerId: 'user-1',
      currentUserId: 'user-1',
    });
    expect(mockedHasTaskIntent).toHaveBeenNthCalledWith(2, {
      realBotId: 'bot-b',
      botUuid: 'bot-b:user-1',
      ownerId: 'user-1',
      currentUserId: 'user-1',
    });
    expect(mockedHasTaskIntent).toHaveBeenCalledTimes(2);
  });

  it('未安装 task-intake 时动态任务不可选', async () => {
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage: jest.fn(),
      }),
    );

    await waitFor(() => expect(result.current.dynamicTaskChecking).toBe(false));
    expect(result.current.dynamicTaskEnabled).toBe(false);
    act(() => result.current.selectDynamic());
    expect(result.current.selections).toEqual([]);
  });

  it('技能探测 Promise 异常 → 安全降级为不可点且不向上抛', async () => {
    mockedHasTaskIntent.mockRejectedValueOnce(new Error('Not Found'));
    const { result } = renderHook(() =>
      useTaskExecution({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage: jest.fn(),
      }),
    );

    await waitFor(() => expect(result.current.dynamicTaskChecking).toBe(false));
    expect(result.current.dynamicTaskEnabled).toBe(false);
    act(() => result.current.selectDynamic());
    expect(result.current.selections).toEqual([]);
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
    mockedHasTaskIntent.mockResolvedValue({ ok: true, data: true });
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

  it('多工作流可叠加、同一工作流按 workflowId 去重', async () => {
    const { result } = renderExecution();
    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));
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

  it('动态任务全场至多一个，可与工作流共存', async () => {
    const { result } = renderExecution();
    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));
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

  it('removeSelection 按 key 单独清除，clearSelection 全清', async () => {
    const { result } = renderExecution();
    await waitFor(() => expect(result.current.dynamicTaskEnabled).toBe(true));
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
