/** @jest-environment jsdom */
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSkillService');

const mockedListSkills = botSkillService.listSkills as jest.Mock;

const bot: ChatBotView = {
  botId: 'bot:2088',
  realBotId: 'bot',
  ownerId: '2088',
  displayName: 'Bot',
  online: true,
  chatable: true,
};

beforeEach(() => {
  mockedListSkills.mockReset().mockResolvedValue({ ok: true, data: [] });
});

it('托管 Bot 保持拉取 Skills', async () => {
  renderHook(() => useBotSkills(bot, 'human_900003'));

  await waitFor(() => expect(mockedListSkills).toHaveBeenCalledWith(bot, 'human_900003'));
});

it('好友 Bot 不拉取 Skills', async () => {
  const { result } = renderHook(() => useBotSkills({ ...bot, isFriendBot: true }, 'human_900003'));

  await waitFor(() => expect(result.current.isLoading).toBe(false));
  expect(mockedListSkills).not.toHaveBeenCalled();
  expect(result.current.skills).toEqual([]);
});

it('懒加载模式不在进入会话时请求，打开子面板时才加载并合并在途请求', async () => {
  const { result } = renderHook(() => useBotSkills(bot, 'viewer', false));
  expect(mockedListSkills).not.toHaveBeenCalled();
  await act(async () => {
    const first = result.current.loadSkills();
    expect(result.current.loadSkills()).toBe(first);
    await first;
  });
  expect(mockedListSkills).toHaveBeenCalledTimes(1);
  await act(async () => { await result.current.loadSkills(); });
  expect(mockedListSkills).toHaveBeenCalledTimes(1);
});

it('切 Bot 后忽略旧 Skills 响应，新的 owner 或阶段不会复用旧明细', async () => {
  type Result = Awaited<ReturnType<typeof botSkillService.listSkills>>;
  let finish!: (value: Result) => void;
  mockedListSkills.mockImplementationOnce(() => new Promise<Result>((resolve) => { finish = resolve; }));
  const { result, rerender } = renderHook(({ target }) => useBotSkills(target, 'viewer', false), {
    initialProps: { target: bot },
  });
  let oldRequest!: Promise<unknown>;
  act(() => { oldRequest = result.current.loadSkills(); });
  const next = { ...bot, ownerId: 'other-owner', runtimeStage: 'online' as const };
  rerender({ target: next });
  await act(async () => { finish({ ok: true, data: [] }); await oldRequest; });
  await act(async () => { await result.current.loadSkills(); });
  expect(mockedListSkills).toHaveBeenLastCalledWith(next, 'viewer');
  expect(mockedListSkills).toHaveBeenCalledTimes(2);
});
