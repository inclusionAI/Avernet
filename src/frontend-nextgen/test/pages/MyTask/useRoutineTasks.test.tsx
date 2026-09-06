/** @jest-environment jsdom */
import { useRoutineTasks } from '@/pages/MyTask/hooks/useRoutineTasks';
import {
  fetchScheduledRoutines,
  type ScheduledRoutinePageResult,
  type ScheduledRoutineRecord,
} from '@/services/scheduledTasks';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/scheduledTasks', () => ({
  fetchOwnerScheduledRoutines: jest.fn(),
  fetchScheduledRoutineDetail: jest.fn(),
  fetchScheduledRoutineRuns: jest.fn(),
  fetchScheduledRoutines: jest.fn(),
  triggerScheduledRoutine: jest.fn(),
}));

const mockedFetchScheduledRoutines = fetchScheduledRoutines as jest.MockedFunction<typeof fetchScheduledRoutines>;

function routine(botId: string): ScheduledRoutineRecord {
  return {
    id: `routine-${botId}`,
    botId,
    botName: botId,
    name: `任务-${botId}`,
    model: 'test-model',
    frequency: 'daily',
    raw: {},
  } as ScheduledRoutineRecord;
}

describe('useRoutineTasks identity request isolation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('Bot 身份切换后忽略旧 Bot 列表请求的晚返回结果', async () => {
    let resolveOld: ((value: ScheduledRoutinePageResult) => void) | undefined;
    let resolveNew: ((value: ScheduledRoutinePageResult) => void) | undefined;
    mockedFetchScheduledRoutines.mockImplementation((botId) => {
      return new Promise((resolve) => {
        if (botId === 'bot-old') resolveOld = resolve;
        else resolveNew = resolve;
      }) as ReturnType<typeof fetchScheduledRoutines>;
    });

    const { result, rerender } = renderHook(
      ({ botId }) => useRoutineTasks(botId, [{ botId, botName: botId }], 1, 10, null, false, true),
      { initialProps: { botId: 'bot-old' } },
    );
    await waitFor(() =>
      expect(mockedFetchScheduledRoutines).toHaveBeenCalledWith('bot-old', { page: 1, page_size: 10 }),
    );

    rerender({ botId: 'bot-new' });
    await waitFor(() =>
      expect(mockedFetchScheduledRoutines).toHaveBeenCalledWith('bot-new', { page: 1, page_size: 10 }),
    );
    await act(async () => {
      resolveNew?.({ items: [routine('bot-new')], total: 1, page: 1, pageSize: 10 });
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.routines.map((item) => item.botId)).toEqual(['bot-new']));

    await act(async () => {
      resolveOld?.({ items: [routine('bot-old')], total: 1, page: 1, pageSize: 10 });
      await Promise.resolve();
    });
    expect(result.current.routines.map((item) => item.botId)).toEqual(['bot-new']);
  });
});
