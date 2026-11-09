/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { ConversationTargetPlaceholder } from '@/pages/Workspace/Chat/components/ConversationTargetPlaceholder';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import { fireEvent, render, screen } from '@testing-library/react';

const directory = (): ConversationDirectoryModel => ({
  managedBots: [], teamBots: [], friendBots: [], managedLoading: false, teamLoading: false,
  friendLoading: false, managedError: null, teamError: null, friendError: null,
  retryManaged: jest.fn(), retryTeam: jest.fn(), retryFriend: jest.fn(),
});

it('ends the skeleton once the directory finishes without a unique match', () => {
  const state = directory();
  const { rerender } = render(<ConversationTargetPlaceholder directory={{ ...state, managedLoading: true }} />);
  expect(screen.getByLabelText('加载 Bot 会话')).toBeInTheDocument();
  rerender(<ConversationTargetPlaceholder directory={state} />);
  expect(screen.queryByLabelText('加载 Bot 会话')).not.toBeInTheDocument();
  expect(screen.getByText('无法定位此 Bot')).toBeInTheDocument();
});

it('offers a retry for directory failures', () => {
  const state = { ...directory(), teamError: '目录请求失败' };
  render(<ConversationTargetPlaceholder directory={state} />);
  expect(screen.getByText('加载 Bot 目录失败')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(state.retryManaged).toHaveBeenCalledTimes(1);
  expect(state.retryTeam).toHaveBeenCalledTimes(1);
});
