/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation';
import { ConversationSessionPlaceholder } from '@/pages/Workspace/Chat/components/ConversationSessionPlaceholder';
import { firstPageOf, managedViewOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useConversationSelection } from '@/pages/Workspace/Chat/hooks/useConversationSelection';
import { useConversationSessionPlaceholder } from '@/pages/Workspace/Chat/hooks/useConversationSessionPlaceholder';
import { useConversationSessions } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore as store } from '@/stores/conversationStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: { listManagedSessions: jest.fn(), listFriendBotSessions: jest.fn() },
}));
jest.mock('@/services/workspace/botSessionService', () => ({
  BOT_SESSION_PAGE_SIZE: 10,
  botSessionService: { createSession: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const a: ConversationBotView = {
  section: 'managed',
  bot: { botId: 'a', realBotId: 'a', displayName: 'Bot A', chatable: true, online: true },
};
const b: ConversationBotView = { ...a, bot: { ...a.bot, botId: 'b', realBotId: 'b', displayName: 'Bot B' } };
const options = { userId: 'user', managedBots: [a, b], friendBots: [] };
const s = (id: string, botId: string) => ({
  sessionId: id,
  botId,
  title: id,
  messageCount: 0,
  gmtCreate: '',
  gmtModified: '',
});
const onOpenList = jest.fn();
function Fixture() {
  const sessions = useConversationSessions(options);
  const selection = useConversationSelection({ ...options, hydrated: true });
  const placeholder = useConversationSessionPlaceholder({ ...options, sessions });
  return (
    <>
      <Button onClick={() => sessions.toggleBot('b', 'managed')}>展开 B</Button>
      {placeholder ? (
        <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={onOpenList} />
      ) : (
        <div data-testid="chat-session">{selection.interactive.session?.title ?? '未选择 Bot'}</div>
      )}
    </>
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  store.getState().reset();
  store.getState().setManagedBotCache('a', managedViewOf('a', 'all', undefined, firstPageOf([s('旧会话', 'a')], 1)));
  store.getState().setExpandedBot('a', true);
  store.getState().selectConversation({
    botId: 'a',
    section: 'managed',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: '旧会话',
  });
  jest.mocked(conversationService.listManagedSessions).mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  jest.mocked(botSessionService.createSession).mockResolvedValue({ ok: true, data: s('新建会话', 'b') });
});
it('empty expanded Bot replaces old chat with creation prompt, and creates/selects its new session', async () => {
  render(<Fixture />);
  expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  expect(await screen.findByText('Bot B 暂无会话')).toBeInTheDocument();
  expect(screen.queryByTestId('chat-session')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
  await waitFor(() => expect(screen.getByTestId('chat-session')).toHaveTextContent('新建会话'));
  expect(botSessionService.createSession).toHaveBeenCalledWith(b.bot, 'user');
  expect(store.getState().sessionsByBotId.b.sessions.total).toBe(1);
});
it('loading does not show old chat or a false empty prompt, then renders the first session', async () => {
  let resolve!: (data: Awaited<ReturnType<typeof conversationService.listManagedSessions>>) => void;
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  render(<Fixture />);
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  expect(screen.getByRole('status', { name: '加载会话列表' })).toBeInTheDocument();
  expect(screen.queryByText('Bot B 暂无会话')).not.toBeInTheDocument();
  expect(screen.queryByTestId('chat-session')).not.toBeInTheDocument();
  await act(async () => resolve({ ok: true, data: { items: [s('B首条', 'b'), s('B次条', 'b')], total: 2 } }));
  expect(screen.getByTestId('chat-session')).toHaveTextContent('B首条');
});
it('failed list shows retry rather than creation; retry selects the first loaded session', async () => {
  jest
    .mocked(conversationService.listManagedSessions)
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAIL', friendlyMessage: '网络失败', canRetry: true } })
    .mockResolvedValueOnce({ ok: true, data: { items: [s('B首条', 'b')], total: 1 } });
  render(<Fixture />);
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  expect(await screen.findByText('网络失败')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '创建会话' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() => expect(screen.getByTestId('chat-session')).toHaveTextContent('B首条'));
});
it('creation failure retains target empty state and supports retry without duplicate clicks', async () => {
  jest
    .mocked(botSessionService.createSession)
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAIL', friendlyMessage: '创建失败', canRetry: true } });
  render(<Fixture />);
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  await screen.findByText('Bot B 暂无会话');
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
  });
  expect(screen.getByText('Bot B 暂无会话')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
  await waitFor(() => expect(screen.getByTestId('chat-session')).toHaveTextContent('新建会话'));
});
it('empty favorites can switch to all before creating, without creating while filtering favorites', async () => {
  store.getState().setManagedBotScope('b', 'favorite');
  render(<Fixture />);
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  expect(await screen.findByText('暂无已收藏会话')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看全部会话' }));
  await screen.findByText('Bot B 暂无会话');
  expect(store.getState().selectedScope).toBe('all');
  expect(botSessionService.createSession).not.toHaveBeenCalled();
});
it('empty state retains the mobile session-list entry', async () => {
  render(<Fixture />);
  fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
  await screen.findByText('Bot B 暂无会话');
  fireEvent.click(screen.getByRole('button', { name: '打开会话列表' }));
  expect(onOpenList).toHaveBeenCalledTimes(1);
});
