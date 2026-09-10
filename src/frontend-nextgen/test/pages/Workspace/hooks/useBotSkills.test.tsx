/** @jest-environment jsdom */
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSkillService } from '@/services/workspace/botSkillService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

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
