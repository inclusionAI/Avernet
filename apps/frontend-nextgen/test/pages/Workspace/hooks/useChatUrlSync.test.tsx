/** @jest-environment jsdom */
import { useChatUrlSync } from '@/pages/Workspace/hooks/useChatUrlSync';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() } }));

const mockedReplace = history.replace as jest.MockedFunction<typeof history.replace>;

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  window.history.replaceState({}, '', '/workspace');
});

it('单聊 URL 同时写入 current 身份和 bot 目标', () => {
  useWorkspaceStore.setState({ activeIdentityId: 'bot-current' });

  renderHook(() => useChatUrlSync(true, 'bot-current', 'friend-bot', 'session-1'));

  expect(mockedReplace).toHaveBeenCalledWith(
    '/workspace?tab=chat&current=bot-current&bot=friend-bot&session=session-1',
  );
});

it('单聊 URL 在没有会话时仍保留目标 Bot', () => {
  useWorkspaceStore.setState({ activeIdentityId: 'human-current' });

  renderHook(() => useChatUrlSync(true, 'human-current', 'empty-bot', undefined));

  expect(mockedReplace).toHaveBeenCalledWith('/workspace?tab=chat&current=human-current&bot=empty-bot');
});
