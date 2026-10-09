/** @jest-environment jsdom */
import type { BotCapabilitySet } from '@/domain/botEditor';
import { useSkillSetActivation } from '@/hooks/useSkillSetActivation';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({ botEditorService: { setSkillSetActive: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() } }));

const service = jest.mocked(botEditorService.setSkillSetActive);
const skillSet: BotCapabilitySet = {
  id: 'set-1',
  name: '个人能力集',
  isDefault: false,
  active: true,
  skills: [],
  mcps: [],
  clis: [],
};
function useHarness(initialActive = true) {
  const [sets, setSets] = useState([{ ...skillSet, active: initialActive }]);
  return { sets, ...useSkillSetActivation('bot-1', setSets) };
}
const response = (active: boolean) => ({ id: 'set-1', active });

afterEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
});

test.each([false, true])('启停共用等待态，服务端确认后才更新开关：目标 %s', async (active) => {
  let finish!: (value: ReturnType<typeof response>) => void;
  service.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = renderHook(() => useHarness(!active));
  await act(async () => {
    void result.current.setSkillSetActive({ ...skillSet, active: !active }, active);
  });
  expect(result.current.pendingSkillSetToggle).toMatchObject({ id: 'set-1', active, slow: false });
  expect(result.current.sets[0].active).toBe(!active);
  await act(async () => {
    finish(response(active));
  });
  await waitFor(() => expect(result.current.pendingSkillSetToggle).toBeUndefined());
  expect(result.current.sets[0].active).toBe(active);
  expect(toast.success).toHaveBeenCalledWith(active ? '能力集已启用' : '能力集已停用');
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
    void result.current.setSkillSetActive(skillSet, false);
    void result.current.setSkillSetActive(skillSet, false);
  });
  expect(service).toHaveBeenCalledTimes(1);
  await act(async () => {
    fail(new Error('停用失败'));
  });
  expect(result.current.sets[0].active).toBe(true);
  expect(result.current.pendingSkillSetToggle).toBeUndefined();
  expect(toast.error).toHaveBeenCalledWith('停用失败');
});

test('响应状态与操作目标不一致时按服务端值展示并提示确认', async () => {
  service.mockResolvedValue({ id: 'set-1', active: true });
  const { result } = renderHook(useHarness);
  await act(async () => {
    await result.current.setSkillSetActive(skillSet, false);
  });
  expect(result.current.sets[0].active).toBe(true);
  expect(toast.warning).toHaveBeenCalledWith('能力集状态与操作目标不一致，请刷新后确认');
  expect(result.current.pendingSkillSetToggle).toBeUndefined();
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
    void result.current.setSkillSetActive(skillSet, false);
  });
  act(() => {
    jest.advanceTimersByTime(8000);
  });
  expect(result.current.pendingSkillSetToggle).toMatchObject({ id: 'set-1', active: false, slow: true });
  expect(result.current.sets[0].active).toBe(true);
});
