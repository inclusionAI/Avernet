/** @jest-environment jsdom */
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useSearchParams } from '@umijs/max';
import { act, renderHook } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useSearchParams: jest.fn(),
}));
jest.mock('@/services/workspace/sessionService', () => ({
  sessionService: { getSessionDetail: jest.fn() },
}));

const mockedUseSearchParams = useSearchParams as jest.MockedFunction<typeof useSearchParams>;

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  mockedUseSearchParams.mockReturnValue([
    new URLSearchParams('tab=chat&bot=bot-1%3A2088'),
    jest.fn(),
  ] as unknown as ReturnType<typeof useSearchParams>);
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_2088', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:2088', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:2088',
    view: 'group',
    selectedBotSessionId: 'old-session',
  });
});

it('bot-only 单聊 URL 恢复用户身份并展开对应 Bot', async () => {
  renderHook(() => useWorkspacePage());
  await act(async () => Promise.resolve());

  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_2088');
  expect(state.view).toBe('chat');
  expect(state.expandedBotIds).toEqual({ 'bot-1:2088': true });
  expect(state.expandedBotSectionKey['bot-1:2088']).toBe('mine');
  expect(state.selectedBotSessionId).toBeNull();
  expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
});
