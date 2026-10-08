/** @jest-environment jsdom */
import { deriveTaskTitle, useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { buildTaskLaunchMessage } from '@/services/tasks/taskPanelMessage';
import { executeTaskService } from '@/services/tasks/taskService';
import { setTaskExecuteHandler } from '@/services/workspace/chatBridge';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/chatBridge', () => ({
  setTaskExecuteHandler: jest.fn(),
}));
jest.mock('@/services/tasks/taskPreflightMock', () => ({
  runTaskPreflightMock: jest.fn().mockResolvedValue({ matched: false, message: '' }),
}));
jest.mock('@/services/tasks/taskService', () => ({
  executeTaskService: jest.fn(),
}));
jest.mock('@/services/tasks/taskPanelMessage', () => ({
  buildTaskPanelAixUI: jest.fn().mockReturnValue('<AixUI-panel/>'),
  buildTaskLaunchMessage: jest.fn(
    (panel: string, record: { task_info?: { execution_config?: { orchestration_mode?: string } } }) =>
      record.task_info?.execution_config?.orchestration_mode === 'relay'
        ? { content: `${panel}\n[relay-root-execution]`, shouldTriggerBot: true }
        : { content: panel, shouldTriggerBot: false },
  ),
}));

const mockedSetHandler = setTaskExecuteHandler as unknown as jest.Mock;
const mockedExecute = executeTaskService as unknown as jest.Mock;
const mockedBuildTaskLaunchMessage = buildTaskLaunchMessage as unknown as jest.Mock;

const context: TaskComposerContext = {
  sourceType: 'bot',
  ownerUserId: 'u1',
  ownerBotId: 'b1',
  mainSessionId: 's1',
  mainSessionName: '会话',
  parentTaskId: null,
};
const task = { goal: '修复 PR #1', deliverables: ['代码 PR'], task_type: 'dynamic' as const };

describe('useTaskExecuteFromCard 卡片执行', () => {
  let registeredHandler: ((taskRaw: Record<string, unknown>) => void) | null = null;

  beforeEach(() => {
    jest.clearAllMocks();
    registeredHandler = null;
    mockedSetHandler.mockImplementation((h: ((t: Record<string, unknown>) => void) | null) => {
      registeredHandler = h;
    });
    mockedExecute.mockResolvedValue({
      task_id: 't-1',
      create_time: 1,
      finish_time: 2,
      task_info: { execution_config: {} },
    });
  });

  it('点击确认后直接调用 executeTaskService 并发送副屏面板消息', async () => {
    const submitPanelMessage = jest.fn();

    renderHook(() =>
      useTaskExecuteFromCard({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage,
      }),
    );

    await act(async () => {
      registeredHandler?.(task);
    });
    await waitFor(() => expect(mockedExecute).toHaveBeenCalled());
    expect(submitPanelMessage).toHaveBeenCalledTimes(1);
    expect(submitPanelMessage).toHaveBeenCalledWith('<AixUI-panel/>');
    expect(mockedBuildTaskLaunchMessage).toHaveBeenCalledWith(
      '<AixUI-panel/>',
      expect.objectContaining({ task_id: 't-1' }),
      expect.any(Object),
    );
  });

  it('接力模式 → 在当前主 Bot 会话直接注入根节点执行指令，不经过 Runner 派发根节点', async () => {
    mockedExecute.mockResolvedValue({
      task_id: 't-relay',
      create_time: 1,
      finish_time: null,
      task_info: {
        execution_config: { orchestration_mode: 'relay', root_node_id: 'root-relay' },
      },
    });
    const submitPanelMessage = jest.fn();
    const submitTaskExecutionMessage = jest.fn();

    renderHook(() =>
      useTaskExecuteFromCard({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage,
        submitTaskExecutionMessage,
      }),
    );

    await act(async () => {
      registeredHandler?.(task);
    });
    await waitFor(() => expect(submitTaskExecutionMessage).toHaveBeenCalledTimes(1));

    expect(mockedBuildTaskLaunchMessage).toHaveBeenCalledWith(
      '<AixUI-panel/>',
      expect.objectContaining({ task_id: 't-relay' }),
      expect.objectContaining({ holderId: 'b1', objective: task.goal }),
    );
    expect(submitTaskExecutionMessage).toHaveBeenCalledWith('<AixUI-panel/>\n[relay-root-execution]', 'b1');
    expect(submitPanelMessage).not.toHaveBeenCalled();
  });
});

describe('deriveTaskTitle —— 从 goal 派生 ≤20 字短标题（协议 §4.3.1 平台生成短标题）', () => {
  it('短 goal 原样返回', () => {
    expect(deriveTaskTitle('修复 PR #1')).toBe('修复 PR #1');
  });
  it('取首个分句（逗号切）', () => {
    expect(deriveTaskTitle('修复 PR 命名问题，并补充测试用例')).toBe('修复 PR 命名问题');
  });
  it('取首个分句（换行/句末标点切）', () => {
    expect(deriveTaskTitle('大促 GMV 增长目标。补齐营销策略')).toBe('大促 GMV 增长目标');
    expect(deriveTaskTitle('目标A\n目标B')).toBe('目标A');
  });
  it('超 20 字的首分句截 19 字+…，结果 ≤20 字且为原文前缀', () => {
    const goal = '大促GMV增长目标加长版活动营销场景下做营销策略补齐方案';
    const t = deriveTaskTitle(goal);
    expect(t.length).toBeLessThanOrEqual(20);
    expect(t.endsWith('…')).toBe(true);
    expect(goal.startsWith(t.slice(0, 19))).toBe(true);
  });
  it('恰好 20 字（边界）不带省略号', () => {
    const exact = '一二三四五六七八九十一二三四五六七八九十'; // 20 字
    expect(exact.length).toBe(20);
    expect(deriveTaskTitle(exact)).toBe(exact);
  });
  it('空/缺省 goal 兜底「任务执行」', () => {
    expect(deriveTaskTitle('')).toBe('任务执行');
    expect(deriveTaskTitle(undefined)).toBe('任务执行');
    expect(deriveTaskTitle(null)).toBe('任务执行');
    expect(deriveTaskTitle('   \n  ')).toBe('任务执行');
  });
});
