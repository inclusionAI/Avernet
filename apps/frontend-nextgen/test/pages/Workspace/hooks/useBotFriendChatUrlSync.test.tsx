/** @jest-environment jsdom */
import { useBotFriendChatUrlSync } from '@/pages/Workspace/hooks/useBotFriendChatUrlSync';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { replace: require('jest-mock').fn() } }));

const replace = history.replace as jest.MockedFunction<typeof history.replace>;

beforeEach(() => {
  jest.clearAllMocks();
  replace.mockReset();
  useWorkspaceStore.getState().reset();
  window.history.replaceState({}, '', '/workspace');
});

it('writes current Bot, Human friend and Session to the canonical URL', () => {
  renderHook(() => useBotFriendChatUrlSync(true, 'bot-a:327325', '447147', 'session-1'));

  expect(replace).toHaveBeenCalledWith('/workspace?tab=chat&current=bot-a%3A327325&human=447147&session=session-1');
});

it('keeps the Human target when no Session is selected', () => {
  renderHook(() => useBotFriendChatUrlSync(true, 'bot-a:327325', '447147', null));

  expect(replace).toHaveBeenCalledWith('/workspace?tab=chat&current=bot-a%3A327325&human=447147');
});

it('waits for the selected Session cache instead of dropping session from the URL', () => {
  useWorkspaceStore.setState({ selectedFriendUserSessionId: 'session-1' });

  renderHook(() => useBotFriendChatUrlSync(true, 'bot-a:327325', '447147', null));

  expect(replace).not.toHaveBeenCalled();
});
