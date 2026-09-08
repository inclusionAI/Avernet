/** @jest-environment jsdom */
import { deriveTaskTitle, useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { isBotTaskClaimEnabled } from '@/services/tasks/taskClaimQuery';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { executeTaskService } from '@/services/tasks/taskService';
import { setTaskExecuteHandler } from '@/services/workspace/chatBridge';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/chatBridge', () => ({
  setTaskExecuteHandler: jest.fn(),
}));
jest.mock('@/services/tasks/taskClaimQuery', () => ({
  isBotTaskClaimEnabled: jest.fn(),
}));
jest.mock('@/services/tasks/taskPreflightMock', () => ({
  runTaskPreflightMock: jest.fn().mockResolvedValue({ matched: false, message: '' }),
}));
jest.mock('@/services/tasks/taskService', () => ({
  executeTaskService: jest.fn(),
}));
jest.mock('@/services/tasks/taskPanelMessage', () => ({
  buildTaskPanelAixUI: jest.fn().mockReturnValue('<AixUI-panel/>'),
}));
jest.mock('sonner', () => ({
  toast: { warning: jest.fn(), info: jest.fn(), error: jest.fn() },
}));

const mockedSetHandler = setTaskExecuteHandler as unknown as jest.Mock;
const mockedIsClaimEnabled = isBotTaskClaimEnabled as unknown as jest.Mock;
const mockedExecute = executeTaskService as unknown as jest.Mock;
const mockedToastWarning = toast.warning as unknown as jest.Mock;

const context: TaskComposerContext = {
  sourceType: 'bot',
  ownerUserId: 'u1',
  ownerBotId: 'b1',
  mainSessionId: 's1',
  mainSessionName: '会话',
  parentTaskId: null,
};
const task = { goal: '修复 PR #1', deliverables: ['代码 PR'], task_type: 'dynamic' as const };

describe('useTaskExecuteFromCard 执行前任务认领门禁', () => {
  let registeredHandler: ((taskRaw: Record<string, unknown>) => void) | null = null;

  beforeEach(() => {
    jest.clearAllMocks();
    registeredHandler = null;
    mockedSetHandler.mockImplementation((h: ((t: Record<string, unknown>) => void) | null) => {
      registeredHandler = h;
    });
    mockedExecute.mockResolvedValue({ task_id: 't-1', create_time: 1, finish_time: 2 });
  });

  it('Bot 未开启任务认领 → 提示去任务协作页授权、阻断执行且不调 executeTaskService', async () => {
    mockedIsClaimEnabled.mockResolvedValue(false);
    const submitPanelMessage = jest.fn();
    const onOpenCollaborationPermissions = jest.fn();

    renderHook(() =>
      useTaskExecuteFromCard({
        panelRef: { current: null } as never,
        context,
        submitPanelMessage,
        onOpenCollaborationPermissions,
      }),
    );

    await act(async () => {
      registeredHandler?.(task);
    });

    expect(mockedToastWarning).toHaveBeenCalledWith(
      '当前 Bot 未开启任务认领，请先去任务协作页对当前 Bot 授权开启后再执行',
      expect.objectContaining({ action: expect.objectContaining({ label: '去开启' }) }),
    );
    const action = mockedToastWarning.mock.calls[0]?.[1]?.action;
    expect(action?.onClick).toEqual(expect.any(Function));
    action?.onClick();
    expect(onOpenCollaborationPermissions).toHaveBeenCalledTimes(1);
    expect(mockedExecute).not.toHaveBeenCalled();
    expect(submitPanelMessage).not.toHaveBeenCalled();
  });

  it('Bot 已开启任务认领 → 放行，调 executeTaskService 后发副屏面板消息', async () => {
    mockedIsClaimEnabled.mockResolvedValue(true);
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
  });

  it('查询任务认领异常（开源无鉴权/网络抖动）→ 放行，不阻断执行', async () => {
    mockedIsClaimEnabled.mockResolvedValue(true);
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
    expect(mockedToastWarning).not.toHaveBeenCalled();
  });

  it('onOpenCollaborationPermissions 缺省 → 阻断 toast 不带跳转 action', async () => {
    mockedIsClaimEnabled.mockResolvedValue(false);
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

    expect(mockedToastWarning).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ action: undefined }));
    expect(mockedExecute).not.toHaveBeenCalled();
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
