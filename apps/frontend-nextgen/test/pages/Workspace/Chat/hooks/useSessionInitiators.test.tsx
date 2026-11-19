/** @jest-environment jsdom */
import type { SessionInitiator } from '@/domain/conversation/sessionInitiator';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { ConversationSessionList } from '@/pages/Workspace/Chat/components/ConversationSessionList';
import { firstPageOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useSessionInitiators } from '@/pages/Workspace/Chat/hooks/useSessionInitiators';
import { loadSessionInitiators } from '@/services/workspace/sessionInitiatorService';
import '@testing-library/jest-dom';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity');
jest.mock('@/services/workspace/sessionInitiatorService');
const load = jest.mocked(loadSessionInitiators);
const identity = jest.mocked(useHumanIdentity);
const sessionId = 'agent:main:session:abc:user:123';
beforeEach(() => {
  load.mockReset();
  load.mockResolvedValue({});
  identity.mockReturnValue({ identity: { userId: 'viewer', displayName: '当前人', online: true }, status: 'ready' });
});

it('enriches asynchronously and discards another session/viewer late responses', async () => {
  let finish!: (value: Record<string, SessionInitiator>) => void;
  load.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, rerender } = renderHook(({ id }) => useSessionInitiators([id]), { initialProps: { id: sessionId } });
  expect(result.current(sessionId)).toEqual({ userId: '123' });
  identity.mockReturnValue({ identity: { userId: 'new-viewer', displayName: '新身份', online: true }, status: 'ready' });
  const second = 'user:456:session:xyz';
  load.mockResolvedValue({ '456': { userId: '456', displayName: '李四' } });
  rerender({ id: second });
  await waitFor(() => expect(result.current(second)?.displayName).toBe('李四'));
  await act(async () => finish({ '123': { userId: '123', displayName: '旧姓名' } }));
  expect(result.current(second)?.displayName).toBe('李四');
  expect(result.current(sessionId)?.displayName).toBeUndefined();
});

it('shows a compact initiator badge independently of the session title and remains read-only', async () => {
  load.mockResolvedValue({ '123': { userId: '123', displayName: '张三' } });
  render(
    <ConversationSessionList
      sessions={firstPageOf(
        [{ sessionId, botId: 'bot', title: '讨论发布', messageCount: 1, gmtCreate: '', gmtModified: '' }],
        1,
      )}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      isFavoritePending={() => false}
      showInitiator
      showCreateHint={false}
    />,
  );
  expect(screen.getByText('讨论发布')).toBeInTheDocument();
  expect(await screen.findByLabelText('发起人：张三')).toBeInTheDocument();
  expect(screen.getByText('只读')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
});

it('falls back to the explicit ID or 用户 without inventing names', () => {
  const sessions = [sessionId, 'opaque-session'].map((id) => ({
    sessionId: id,
    botId: 'bot',
    title: id,
    messageCount: 0,
    gmtCreate: '',
    gmtModified: '',
  }));
  render(
    <ConversationSessionList
      sessions={firstPageOf(sessions, 2)}
      selectedSessionId={null}
      onSelectSession={jest.fn()}
      onLoadMore={jest.fn()}
      isFavoritePending={() => false}
      showInitiator
    />,
  );
  expect(screen.getByLabelText('发起人：123')).toBeInTheDocument();
  expect(screen.getByLabelText('发起人：用户')).toBeInTheDocument();
});

it('does not request names for the normal mine list', async () => {
  renderHook(() => useSessionInitiators([sessionId], false));
  expect(load).not.toHaveBeenCalled();
});
