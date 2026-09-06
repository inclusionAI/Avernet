/** @jest-environment jsdom */
import type { TaskListItem } from '@/domain/tasks/models';
import { useMyTaskTasks } from '@/pages/MyTask/hooks/useMyTaskTasks';
import { listMyTasks, type ListMyTaskResult } from '@/services/myTask';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/myTask', () => ({
  isEnvelopeFailure: jest.fn(() => false),
  listMyTasks: jest.fn(),
  normalizeMyTaskPage: jest.fn((data: unknown) => ({ items: Array.isArray(data) ? data : [], total: 0 })),
  runtimeStatusesFromProductFilter: jest.fn(() => undefined),
}));

const mockedListMyTasks = listMyTasks as jest.MockedFunction<typeof listMyTasks>;

describe('useMyTaskTasks enabled gate', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Bot 工作身份禁用时不请求用户任务', async () => {
    const { result } = renderHook(() => useMyTaskTasks('327325', 1, 10, 'all', false));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockedListMyTasks).not.toHaveBeenCalled();
    expect(result.current.taskRecords).toEqual([]);
  });

  it('身份切换后忽略旧用户任务请求的晚返回结果', async () => {
    let resolveRequest: ((value: ListMyTaskResult) => void) | undefined;
    mockedListMyTasks.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }) as ReturnType<typeof listMyTasks>,
    );
    const { result, rerender } = renderHook(({ enabled }) => useMyTaskTasks('327325', 1, 10, 'all', enabled), {
      initialProps: { enabled: true },
    });

    await waitFor(() => expect(mockedListMyTasks).toHaveBeenCalledTimes(1));
    rerender({ enabled: false });
    await act(async () => {
      resolveRequest?.({ code: 0, message: 'ok', data: [{ task_id: 'old-task' } as TaskListItem] });
      await Promise.resolve();
    });

    expect(result.current.taskRecords).toEqual([]);
    expect(result.current.loading).toBe(false);
  });
});
