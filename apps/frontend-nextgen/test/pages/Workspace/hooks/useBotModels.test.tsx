/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { BotModelSelectorContainer } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { useBotModels } from '@/pages/Workspace/hooks/useBotModels';
import { clearBotModelCache } from '@/services/workspace/botModelCache';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

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
  clearBotModelCache();
  // 恢复默认形态（Open Core 无徽标数据），避免用例间 capability 注入残留。
  extendCapabilities({
    getModelBadge: () => ({ status: 'available', value: null }),
  });
  mockedListModels.mockReset().mockResolvedValue({
    ok: true,
    data: [{ modelId: 'model-1', name: 'Model 1', provider: 'provider' }],
  });
});

it('capability 注入的模型徽标随模型列表合并', async () => {
  extendCapabilities({
    getModelBadge: () => ({
      status: 'available',
      value: { label: '推荐', link: 'https://example.com/venus' },
    }),
  });
  const { result } = renderHook(() =>
    useBotModels(managedBot, { ...session, botId: managedBot.botId }, 'human_900003', jest.fn()),
  );

  await waitFor(() => expect(result.current.isLoadingModels).toBe(false));
  expect(result.current.models[0]).toMatchObject({
    modelId: 'model-1',
    badge: { label: '推荐', link: 'https://example.com/venus' },
  });
});

it('capability 返回 unsupported 时模型列表保持无徽标（防御分支）', async () => {
  extendCapabilities({
    getModelBadge: () => ({ status: 'unsupported', value: null, reason: '测试注入' }),
  });
  const { result } = renderHook(() =>
    useBotModels(managedBot, { ...session, botId: managedBot.botId }, 'human_900003', jest.fn()),
  );

  await waitFor(() => expect(result.current.isLoadingModels).toBe(false));
  expect(result.current.models[0]).not.toHaveProperty('badge');
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

it('对话页仅打开模型选择器时加载，关闭后重开复用缓存', async () => {
  render(<BotModelSelectorContainer chatBots={[managedBot]} session={{ ...session, botId: managedBot.botId }}
    activeIdentityId="human_900003" onSessionModelChange={jest.fn()} />);
  expect(mockedListModels).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /session-model/ }));
  await screen.findByText('Model 1');
  expect(mockedListModels).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(document, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByText('Model 1')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: /session-model/ }));
  await screen.findByText('Model 1');
  expect(mockedListModels).toHaveBeenCalledTimes(1);
});

it('模型请求未完成切换 Bot 时，旧响应不覆盖新上下文', async () => {
  type Result = Awaited<ReturnType<typeof botSessionService.listModels>>;
  let finishOld!: (value: Result) => void;
  mockedListModels.mockImplementationOnce(() => new Promise<Result>((resolve) => { finishOld = resolve; }));
  const { result, rerender } = renderHook(({ bot }) => useBotModels(bot, session, 'viewer', jest.fn()), {
    initialProps: { bot: managedBot },
  });
  rerender({ bot: { ...managedBot, botId: 'new:2088', realBotId: 'new' } });
  await waitFor(() => expect(result.current.models[0]?.modelId).toBe('model-1'));
  await act(async () => finishOld({ ok: true, data: [{ modelId: 'old', name: 'Old', provider: '' }] }));
  expect(result.current.models[0]?.modelId).toBe('model-1');
});
