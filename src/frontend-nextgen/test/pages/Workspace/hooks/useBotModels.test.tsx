/** @jest-environment jsdom */
import { BotModelSelectorContainer } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { useBotModels } from '@/pages/Workspace/hooks/useBotModels';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService');

const mockedListModels = botSessionService.listModels as jest.Mock;

const managedBot: ChatBotView = {
  botId: 'managed-bot:2088',
  realBotId: 'managed-bot',
  ownerId: '2088',
  displayName: 'Managed Bot',
  online: true,
  chatable: true,
};

const friendBot: ChatBotView = { ...managedBot, isFriendBot: true };
const session: BotChatSessionView = {
  sessionId: 'session-1',
  botId: friendBot.botId,
  title: 'Session',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
  model: 'session-model',
};

beforeEach(() => {
  mockedListModels.mockReset().mockResolvedValue({
    ok: true,
    data: [{ modelId: 'model-1', name: 'Model 1', provider: 'provider' }],
  });
});

it('托管 Bot 会话保持拉取模型列表', async () => {
  const { result } = renderHook(() =>
    useBotModels(managedBot, { ...session, botId: managedBot.botId }, 'human_900003', jest.fn()),
  );

  await waitFor(() => expect(result.current.isLoadingModels).toBe(false));
  expect(mockedListModels).toHaveBeenCalledWith(managedBot, 'human_900003');
  expect(result.current.activeModelId).toBe('session-model');
});

it('好友 Bot 会话不拉取模型列表，保留会话模型只读展示', async () => {
  const { result } = renderHook(() => useBotModels(friendBot, session, 'human_900003', jest.fn()));

  await waitFor(() => expect(result.current.isLoadingModels).toBe(false));
  expect(mockedListModels).not.toHaveBeenCalled();
  expect(result.current.models).toEqual([]);
  expect(result.current.activeModelId).toBe('session-model');
});

it('好友 Bot 的模型选择按钮禁用', () => {
  render(
    <BotModelSelectorContainer
      chatBots={[friendBot]}
      session={session}
      activeIdentityId="human_900003"
      onSessionModelChange={jest.fn()}
    />,
  );

  expect(screen.getByRole('button', { name: /session-model/ })).toBeDisabled();
});
