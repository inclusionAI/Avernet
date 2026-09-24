/** @jest-environment jsdom */
import { useComposerSend } from '@/hooks/useComposerSend';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
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
    selectedWorkflow: null,
    pendingDynamic: false,
    selectWorkflow: jest.fn(),
    selectDynamic: jest.fn(),
    clearSelection: jest.fn(),
    submitFromComposer: jest.fn(async () => ({ ok: false as const, reason: 'unused' })),
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
    const execution = taskExecution({ pendingDynamic: true });
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
    expect(sendMessage).toHaveBeenCalledWith('/task 调研首条消息标题', undefined);
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
