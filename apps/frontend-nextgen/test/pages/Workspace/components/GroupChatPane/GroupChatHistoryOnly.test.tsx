/** @jest-environment jsdom */
import { GroupChatPane } from '@/pages/Workspace/components/GroupChatPane';
import { expect, it, jest } from '@jest/globals';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/jest-globals';
import { createRef, type ComponentProps } from 'react';

jest.mock('@tc-chat/ui/es/ChatLayout', () => ({ ChatLayout: ({ children }: any) => <div>{children}</div> }));
jest.mock('@/components/Workspace/ChatLayoutPanelSlot', () => ({ ChatLayoutPanelSlot: () => null }));
jest.mock('@/pages/Workspace/components/GroupHeader', () => ({ GroupHeader: () => null }));
jest.mock('@/pages/Workspace/components/GroupChatPane/FuseSlot', () => ({ FuseSlot: () => null }));
jest.mock('@/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel', () => ({ GroupChatCollabPanel: () => <div>加入会话</div> }));
jest.mock('@/pages/Workspace/components/GroupChatPane/GroupChatComposer', () => ({ GroupChatComposer: () => <div>composer</div> }));
jest.mock('@/pages/Workspace/components/GroupChatPane/GroupChatMessageArea', () => ({
  GroupChatMessageArea: ({ isLoadingHistory, showError, messages, onLoadMoreHistory, reloadHistory }: any) =>
    isLoadingHistory ? <div>loading</div> : showError ? <button type="button" onClick={reloadHistory}>retry history</button> :
      <div>{messages.length ? 'history' : 'empty'}<button type="button" onClick={onLoadMoreHistory}>older</button></div>,
}));
jest.mock('@/pages/Workspace/hooks/useCollabPanel', () => ({ useCollabPanel: () => ({ humanAbsentOnly: true }) }));
jest.mock('@/pages/Workspace/hooks/useGroupTaskExecution', () => ({ useGroupTaskExecution: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useMessageEdit', () => ({ useMessageEdit: () => ({}) }));
jest.mock('@/pages/Workspace/hooks/useMessageInteractions', () => ({ useMessageInteractions: () => ({}) }));
jest.mock('@/pages/Workspace/components/GroupChatPane/useGroupChatPaneActions', () => ({ useGroupChatPaneActions: () => ({}) }));

function props(phase: 'ready' | 'loading-history' | 'error', messages: unknown[] = []) {
  return {
    group: { groupId: 'g1' }, session: { sessionId: 's1', participants: [] },
    activeIdentity: { id: 'human_1', kind: 'user' },
    historyOnly: true, connectionStatus: 'disconnected', supportState: { phase, error: phase === 'error' ? 'HTTP failed' : null },
    chat: { messages }, panelRef: createRef(),
  } as unknown as ComponentProps<typeof GroupChatPane>;
}

it('renders read-only history and empty results after HTTP completes', () => {
  const { rerender } = render(<GroupChatPane {...props('loading-history')} />);
  expect(screen.getByText('loading')).toBeInTheDocument();
  rerender(<GroupChatPane {...props('ready', [{ id: 'm1', role: 'user', content: 'hello', status: 'history' }])} />);
  expect(screen.getByText('history')).toBeInTheDocument();
  expect(screen.queryByText('composer')).not.toBeInTheDocument();
  expect(screen.getByText('加入会话')).toBeInTheDocument();
  rerender(<GroupChatPane {...props('ready')} />);
  expect(screen.getByText('empty')).toBeInTheDocument();
});

it('shows the HTTP retry action instead of a perpetual skeleton on history failure', () => {
  render(<GroupChatPane {...props('error')} />);
  expect(screen.getByRole('button', { name: 'retry history' })).toBeInTheDocument();
  expect(screen.queryByText('loading')).not.toBeInTheDocument();
});
