/** @jest-environment jsdom */
import { useComposerSend } from '@/hooks/useComposerSend';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import type { ComposerSelection } from '@/services/tasks/taskMapper';
import { describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

function taskExecution(overrides: Partial<UseTaskExecutionResult> = {}): UseTaskExecutionResult {
  return {
    submitting: false,
    error: null,
    lastTaskId: null,
    workflows: [],
    workflowsLoading: false,
    loadWorkflows: jest.fn(async () => {}),
    selections: [],
    selectWorkflow: jest.fn(),
    selectDynamic: jest.fn(),
    removeSelection: jest.fn(),
    clearSelection: jest.fn(),
    submit: jest.fn(async () => ({ ok: false as const, reason: 'unused' })),
    validate: jest.fn(() => null),
    ...overrides,
  };
}

describe('useComposerSend beforeSend', () => {
  it('等待首发前置操作完成后再发送消息', async () => {
    let finishBeforeSend!: () => void;
    const beforeSend = jest.fn(
      () =>
        new Promise<void>((resolve) => {
          finishBeforeSend = resolve;
        }),
    );
    const sendMessage = jest.fn();
    const { result } = renderHook(() =>
      useComposerSend(taskExecution(), {
        beforeSend,
        sendMessage,
        clearDraft: jest.fn(),
      }),
    );

    let pending!: Promise<void>;
    act(() => {
      pending = result.current('第一条消息');
    });
    expect(beforeSend).toHaveBeenCalledWith('第一条消息');
    expect(sendMessage).not.toHaveBeenCalled();

    await act(async () => {
      finishBeforeSend();
      await pending;
    });
    expect(sendMessage).toHaveBeenCalledWith('第一条消息', undefined);
  });

  it('任务模式用原始正文执行 beforeSend，再发送转换后的 task 指令', async () => {
    const beforeSend = jest.fn(async () => {});
    const sendMessage = jest.fn();
    const execution = taskExecution({ selections: [{ kind: 'dynamic' }] });
    const { result } = renderHook(() =>
      useComposerSend(execution, {
        beforeSend,
        sendMessage,
        clearDraft: jest.fn(),
      }),
    );

    await act(async () => {
      await result.current('  调研首条消息标题  ');
    });

    expect(beforeSend).toHaveBeenCalledWith('  调研首条消息标题  ');
    expect(sendMessage).toHaveBeenCalledWith('/task-loop 调研首条消息标题', undefined);
  });

  it('多个胶囊时逐胶囊发送指令（各发起一个任务），发完清空选中态', async () => {
    const sendMessage = jest.fn();
    const clearSelection = jest.fn();
    const clearDraft = jest.fn();
    const selections: ComposerSelection[] = [
      { kind: 'workflow', workflow: { workflowId: 'wf-1', command: 'cmd-fa', title: '工作流A' } },
      { kind: 'dynamic' },
    ];
    const { result } = renderHook(() =>
      useComposerSend(taskExecution({ selections, clearSelection }), {
        sendMessage,
        clearDraft,
      }),
    );

    await act(async () => {
      await result.current('调研 A');
    });

    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(sendMessage).toHaveBeenNthCalledWith(1, "/task workflow_id='cmd-fa' 调研 A", undefined);
    expect(sendMessage).toHaveBeenNthCalledWith(2, '/task-loop 调研 A', undefined);
    expect(clearSelection).toHaveBeenCalledTimes(1);
    expect(clearDraft).toHaveBeenCalledTimes(1);
  });

  it('beforeSend 失败不阻断消息发送', async () => {
    const beforeSend = jest.fn(async () => {
      throw new Error('重命名失败');
    });
    const sendMessage = jest.fn();
    const { result } = renderHook(() =>
      useComposerSend(taskExecution(), {
        beforeSend,
        sendMessage,
        clearDraft: jest.fn(),
      }),
    );

    await act(async () => {
      await result.current('仍然发送');
    });

    expect(sendMessage).toHaveBeenCalledWith('仍然发送', undefined);
  });
});
