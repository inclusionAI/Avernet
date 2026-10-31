/** @jest-environment jsdom */
import type { BotEditorRoutine } from '@/domain/botEditor';
import { useRoutineActivation } from '@/hooks/useRoutineActivation';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import type { RoutineDto } from '@/services/backendApi/bots/botEditorController';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({ botEditorService: { updateRoutine: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() } }));

const service = jest.mocked(botEditorService.updateRoutine);
const routine: BotEditorRoutine = {
  id: 'routine-1',
  name: '晨报',
  cron: '0 9 * * 1-5',
  command: '整理昨日进展',
  enabled: true,
  timezone: 'Asia/Shanghai',
  model: undefined,
  timeoutSecs: 1800,
  modifiedAt: '2026-08-18T11:20:00+08:00',
  nextRunAt: '2026-08-19T09:00:00+08:00',
};
const dto = (enabled: boolean): RoutineDto => ({
  routine_id: 'routine-1',
  bot_id: 'bot-1',
  name: '晨报',
  trigger: { type: 'schedule', cron: '0 9 * * 1-5' },
  command: '整理昨日进展',
  enabled,
  timezone: 'Asia/Shanghai',
  model: null,
  timeout_secs: 1800,
  gmt_modified: '2026-08-18T11:20:00+08:00',
  next_run_at: '2026-08-19T09:00:00+08:00',
});
function useHarness(initialEnabled = true) {
  const [routines, setRoutines] = useState([{ ...routine, enabled: initialEnabled }]);
  return { routines, ...useRoutineActivation('bot-1', undefined, setRoutines) };
}

afterEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
});

test.each([false, true])('启停共用等待态，服务端确认后才更新开关：目标 %s', async (enabled) => {
  let finish!: (value: { data: RoutineDto }) => void;
  service.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = renderHook(() => useHarness(!enabled));
  await act(async () => {
    void result.current.toggleRoutine({ ...routine, enabled: !enabled });
  });
  expect(result.current.pendingRoutineToggle).toMatchObject({ id: 'routine-1', enabled, slow: false });
  expect(service).toHaveBeenCalledWith(
    'bot-1',
    'routine-1',
    expect.objectContaining({ enabled, timeoutSecs: 1800 }),
    undefined,
  );
  expect(result.current.routines[0].enabled).toBe(!enabled);
  await act(async () => {
    finish({ data: dto(enabled) });
  });
  await waitFor(() => expect(result.current.pendingRoutineToggle).toBeUndefined());
  expect(result.current.routines[0].enabled).toBe(enabled);
  expect(toast.success).toHaveBeenCalledWith(enabled ? '定时任务已启用' : '定时任务已停用');
});

test('请求失败保留旧状态并解除等待态，重复触发只发一次请求', async () => {
  let fail!: (error: Error) => void;
  service.mockImplementation(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  const { result } = renderHook(useHarness);
  await act(async () => {
    void result.current.toggleRoutine(routine);
    void result.current.toggleRoutine(routine);
  });
  expect(service).toHaveBeenCalledTimes(1);
  await act(async () => {
    fail(new Error('停用失败'));
  });
  expect(result.current.routines[0].enabled).toBe(true);
  expect(result.current.pendingRoutineToggle).toBeUndefined();
  expect(toast.error).toHaveBeenCalledWith('停用失败');
});

test('响应状态与操作目标不一致时按服务端值展示并提示确认', async () => {
  service.mockResolvedValue({ data: dto(true) });
  const { result } = renderHook(useHarness);
  await act(async () => {
    await result.current.toggleRoutine(routine);
  });
  expect(result.current.routines[0].enabled).toBe(true);
  expect(toast.warning).toHaveBeenCalledWith('定时任务状态与操作目标不一致，请刷新后确认');
  expect(result.current.pendingRoutineToggle).toBeUndefined();
});

test('慢请求显示仍在等待的状态，不重复提交', async () => {
  jest.useFakeTimers();
  service.mockImplementation(
    () =>
      new Promise(() => {
        /* 模拟仍在途的请求 */
      }),
  );
  const { result } = renderHook(useHarness);
  await act(async () => {
    void result.current.toggleRoutine(routine);
  });
  act(() => {
    jest.advanceTimersByTime(8000);
  });
  expect(result.current.pendingRoutineToggle).toMatchObject({ id: 'routine-1', enabled: false, slow: true });
  expect(result.current.routines[0].enabled).toBe(true);
});
