/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import type { ConversationBotView } from '@/domain/conversation';
import { ConversationSessionPlaceholder } from '@/pages/Workspace/Chat/components/ConversationSessionPlaceholder';
import { PrimaryNavContext } from '@/components/Workspace/PrimaryNav';
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
  botSessionService: { createSession: jest.fn(), deleteSession: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const a: ConversationBotView = {
  section: 'managed',
  bot: { botId: 'a', realBotId: 'a', displayName: 'Bot A', chatable: true, online: true },
};
const b: ConversationBotView = { ...a, bot: { ...a.bot, botId: 'b', realBotId: 'b', displayName: 'Bot B' } };
const friendBotView: ConversationBotView = {
  section: 'friend',
  bot: { botId: 'f', realBotId: 'f', displayName: 'Bot F', chatable: true, online: true },
};
const options = { userId: 'user', managedBots: [a, b], friendBots: [] };
const friendOptions = { userId: 'user', managedBots: [a, b], friendBots: [friendBotView] };
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
function FriendFixture() {
  const sessions = useConversationSessions(friendOptions);
  const placeholder = useConversationSessionPlaceholder({ ...friendOptions, sessions });
  return (
    <>
      <Button onClick={() => sessions.toggleBot('f', 'friend')}>展开 F</Button>
      {placeholder ? (
        <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={onOpenList} />
      ) : (
        <div data-testid="friend-chat">已进入好友会话</div>
      )}
    </>
  );
}
/** 搜索保留回归:打开主会话 + 应用/清空搜索,断言主舞台与占位的真实切换。 */
function SearchFixture() {
  const sessions = useConversationSessions(options);
  const selection = useConversationSelection({ ...options, hydrated: true });
  const placeholder = useConversationSessionPlaceholder({ ...options, sessions });
  return (
    <>
      <Button onClick={() => sessions.selectMineSession('a', '旧会话')}>打开会话</Button>
      <Button onClick={() => sessions.selectMineSession('a', 'zz9')}>打开过滤结果</Button>
      <Button onClick={() => sessions.toggleBot('b', 'managed')}>展开 B</Button>
      <Button onClick={() => store.getState().setSessionKeyFilter('a', 'agent:zz')}>应用搜索</Button>
      <Button onClick={() => store.getState().setSessionKeyFilter('a', '')}>清空搜索</Button>
      {placeholder ? (
        <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={onOpenList} />
      ) : (
        <div data-testid="chat-session">{selection.interactive.session?.title ?? '未选择 Bot'}</div>
      )}
    </>
  );
}
function FriendSearchFixture() {
  const sessions = useConversationSessions(friendOptions);
  const placeholder = useConversationSessionPlaceholder({ ...friendOptions, sessions });
  return (
    <>
      <Button onClick={() => sessions.selectFriendBotSession('f', 'fs1')}>打开好友会话</Button>
      <Button onClick={() => store.getState().setSessionKeyFilter('f', 'agent:zz')}>应用好友搜索</Button>
      {placeholder ? (
        <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={onOpenList} />
      ) : (
        <div data-testid="friend-chat">已进入好友会话</div>
      )}
    </>
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** 无显式写点的选中(URL 深链/删除回退)保留回归:解析出生后由选择 Hook 中央认领快照。 */
function RouteSearchFixture() {
  const sessions = useConversationSessions(options);
  const selection = useConversationSelection({ ...options, hydrated: true });
  const placeholder = useConversationSessionPlaceholder({ ...options, sessions });
  return (
    <>
      <Button onClick={() => selection.onRouteSelection({ section: 'managed', botId: 'a', sessionId: '旧会话' })}>
        深链选中
      </Button>
      <Button onClick={() => void sessions.actions.run('a', 'managed', '旧会话', { type: 'delete' })}>删除会话</Button>
      <Button onClick={() => store.getState().setSessionKeyFilter('a', 'agent:zz')}>搜索未命中</Button>
      <Button onClick={() => store.getState().setSessionKeyFilter('a', 'agent:yy')}>搜索空结果</Button>
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

it('好友 favorite 空列表显示收藏空态,查看全部会话写好友范围并恢复创建入口', async () => {
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  render(<FriendFixture />);
  // 好友 Bot 生效范围读 scopeByFriendBotId(主选中范围恒 all,不随好友筛选变化)。
  act(() => store.getState().setFriendBotScope('f', 'favorite'));
  fireEvent.click(screen.getByRole('button', { name: '展开 F' }));
  expect(await screen.findByText('暂无已收藏会话')).toBeInTheDocument();
  // favorite 空态不给创建入口(不误发创建请求)。
  expect(screen.queryByRole('button', { name: '创建会话' })).not.toBeInTheDocument();
  expect(botSessionService.createSession).not.toHaveBeenCalled();
  expect(store.getState().selectedScope).toBe('all');

  fireEvent.click(screen.getByRole('button', { name: '查看全部会话' }));
  expect(await screen.findByText('Bot F 暂无会话')).toBeInTheDocument();
  const state = store.getState();
  // showAll 按分区路由:好友写 scopeByFriendBotId,且不得误写管理范围 setter。
  expect(state.scopeByFriendBotId['f']).toBe('all');
  expect(state.scopeByManagedBotId['f']).toBeUndefined();
  expect(state.effectiveScopeByManagedBotId['f']).toBeUndefined();
  expect(botSessionService.createSession).not.toHaveBeenCalled();
  // 恢复后的请求携带好友 all 范围。
  expect(conversationService.listFriendBotSessions).toHaveBeenLastCalledWith(
    expect.objectContaining({ botId: 'f' }),
    'user',
    1,
    10,
    'all',
    '',
  );
});

describe('sessionKey 搜索不关闭已打开的主会话', () => {
  it('搜索应用/加载/未命中结果期间保留主会话,清空后按未过滤列表继续解析', async () => {
    const filteredLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    const allLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest
      .mocked(conversationService.listManagedSessions)
      .mockReturnValueOnce(filteredLoad.promise)
      .mockReturnValueOnce(allLoad.promise);
    render(<SearchFixture />);

    fireEvent.click(screen.getByRole('button', { name: '打开会话' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(store.getState().selectedSessionId).toBe('旧会话');

    // 应用搜索:重拉在途 + 未命中结果时,主会话保持打开(spec: 搜索只改变侧栏列表)。
    fireEvent.click(screen.getByRole('button', { name: '应用搜索' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(screen.queryByRole('status', { name: '加载会话列表' })).not.toBeInTheDocument();

    await act(async () => {
      filteredLoad.resolve({ ok: true, data: { items: [s('zz9', 'a')], total: 1 } });
      await filteredLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(store.getState().sessionsByBotId.a.sessions.items.map((item) => item.sessionId)).toEqual(['zz9']);
    expect(store.getState().sessionKeyByBotId.a).toBe('agent:zz');

    // 点击过滤结果行切换主会话;旧会话不得借快照复活。
    fireEvent.click(screen.getByRole('button', { name: '打开过滤结果' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('zz9');

    // 清空搜索:列表恢复,主会话继续解析。
    fireEvent.click(screen.getByRole('button', { name: '清空搜索' }));
    await act(async () => {
      allLoad.resolve({ ok: true, data: { items: [s('旧会话', 'a'), s('zz9', 'a')], total: 2 } });
      await allLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('zz9');
    expect(store.getState().sessionsByBotId.a.sessions.items.map((item) => item.sessionId)).toEqual(['旧会话', 'zz9']);
  });

  it('搜索空结果与失败结果同样不关闭主会话,不显示创建/重试占位', async () => {
    const emptyLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(emptyLoad.promise);
    render(<SearchFixture />);
    fireEvent.click(screen.getByRole('button', { name: '打开会话' }));

    fireEvent.click(screen.getByRole('button', { name: '应用搜索' }));
    await act(async () => {
      emptyLoad.resolve({ ok: true, data: { items: [], total: 0 } });
      await emptyLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(screen.queryByRole('button', { name: '创建会话' })).not.toBeInTheDocument();
    expect(screen.queryByText('Bot A 暂无会话')).not.toBeInTheDocument();
    expect(botSessionService.createSession).not.toHaveBeenCalled();

    const failedLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(failedLoad.promise);
    act(() => {
      store.getState().setSessionKeyFilter('a', 'agent:err');
    });
    await act(async () => {
      failedLoad.resolve({ ok: false, error: { code: 'FAIL', friendlyMessage: '网络失败', canRetry: true } });
      await failedLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(screen.queryByText('网络失败')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });

  it('好友 Bot 搜索同样保持已打开的主会话', async () => {
    jest
      .mocked(conversationService.listFriendBotSessions)
      .mockImplementation(() => Promise.resolve({ ok: true, data: { items: [], total: 0 } }));
    render(<FriendSearchFixture />);
    act(() => {
      store.getState().setFriendBotSessions('f', firstPageOf([s('fs1', 'f')], 1));
    });
    expect(store.getState().friendBotSessionsByBotId.f.items.map((item) => item.sessionId)).toEqual(['fs1']);

    fireEvent.click(screen.getByRole('button', { name: '打开好友会话' }));
    expect(screen.getByTestId('friend-chat')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '应用好友搜索' }));
    await waitFor(() => {
      expect(store.getState().friendBotSessionsByBotId.f?.loading).toBe(true);
    });
    await waitFor(() => {
      expect(store.getState().friendBotSessionsByBotId.f.items).toEqual([]);
    });
    // 过滤后列表为空,已打开的好友主会话仍在舞台,不显示空态/创建占位。
    expect(screen.getByTestId('friend-chat')).toBeInTheDocument();
    expect(screen.queryByText('Bot F 暂无会话')).not.toBeInTheDocument();
  });

  it('范围切换仍按既有语义关闭主会话(保留快照不越权)', async () => {
    render(<SearchFixture />);
    fireEvent.click(screen.getByRole('button', { name: '打开会话' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');

    act(() => {
      store.getState().setManagedBotScope('a', 'favorite');
    });
    expect(screen.queryByTestId('chat-session')).not.toBeInTheDocument();
  });

  it('自动首选的会话在搜索重拉与空结果后仍保持打开', async () => {
    const firstLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(firstLoad.promise);
    render(<SearchFixture />);

    // 真实展开流程:begin 记录首选意图,列表到达后 complete 自动首选首条并保留快照。
    fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
    await act(async () => {
      firstLoad.resolve({ ok: true, data: { items: [s('B首条', 'b')], total: 1 } });
      await firstLoad.promise;
    });
    await waitFor(() => expect(store.getState().selectedSessionId).toBe('B首条'));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('B首条');

    const filteredLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(filteredLoad.promise);
    act(() => {
      store.getState().setSessionKeyFilter('b', 'agent:zz');
    });
    expect(screen.queryByRole('status', { name: '加载会话列表' })).not.toBeInTheDocument();
    expect(screen.getByTestId('chat-session')).toHaveTextContent('B首条');
    await act(async () => {
      filteredLoad.resolve({ ok: true, data: { items: [], total: 0 } });
      await filteredLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('B首条');
    expect(screen.queryByText('Bot B 暂无会话')).not.toBeInTheDocument();
  });

  it('过滤下创建的新会话直接成为主会话,且不前置进过滤列表', async () => {
    jest.mocked(conversationService.listManagedSessions).mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
    render(<SearchFixture />);
    act(() => {
      store.getState().setSessionKeyFilter('b', 'agent:zz');
    });

    fireEvent.click(screen.getByRole('button', { name: '展开 B' }));
    expect(await screen.findByText('Bot B 暂无会话')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '创建会话' }));
    // 创建结果不匹配过滤条件,不得前置进列表;主舞台直接进入新会话,不悬挂空态。
    await waitFor(() => expect(screen.getByTestId('chat-session')).toHaveTextContent('新建会话'));
    expect(store.getState().sessionsByBotId.b.sessions.items).toEqual([]);
    expect(store.getState().sessionsByBotId.b.sessions.total).toBe(0);
    expect(store.getState().selectedSessionId).toBe('新建会话');
    expect(store.getState().selectedSessionSnapshot?.session.sessionId).toBe('新建会话');
  });
});

describe('无显式写点的选中(URL 深链/删除回退)在搜索下不关闭主会话', () => {
  it('URL 深链选中的主会话在搜索加载/未命中/空结果下保持打开', async () => {
    jest
      .mocked(conversationService.listManagedSessions)
      .mockResolvedValue({ ok: true, data: { items: [s('旧会话', 'a'), s('次会话', 'a')], total: 2 } });
    render(<RouteSearchFixture />);

    fireEvent.click(screen.getByRole('button', { name: '深链选中' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');

    const filteredLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(filteredLoad.promise);
    fireEvent.click(screen.getByRole('button', { name: '搜索未命中' }));
    // 重拉在途:不出现加载遮蔽。
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(screen.queryByRole('status', { name: '加载会话列表' })).not.toBeInTheDocument();
    await act(async () => {
      filteredLoad.resolve({ ok: true, data: { items: [s('zz9', 'a')], total: 1 } });
      await filteredLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(store.getState().sessionsByBotId.a.sessions.items.map((item) => item.sessionId)).toEqual(['zz9']);

    const emptyLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(emptyLoad.promise);
    fireEvent.click(screen.getByRole('button', { name: '搜索空结果' }));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    await act(async () => {
      emptyLoad.resolve({ ok: true, data: { items: [], total: 0 } });
      await emptyLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');
    expect(screen.queryByText('Bot A 暂无会话')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建会话' })).not.toBeInTheDocument();
  });

  it('删除回退选中的会话在搜索后保持打开', async () => {
    jest.mocked(botSessionService.deleteSession).mockResolvedValue({ ok: true, data: null });
    jest.mocked(conversationService.listManagedSessions).mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
    render(<RouteSearchFixture />);
    act(() => {
      store
        .getState()
        .setManagedBotCache(
          'a',
          managedViewOf('a', 'all', undefined, firstPageOf([s('旧会话', 'a'), s('次会话', 'a')], 2)),
        );
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('旧会话');

    // 删除当前会话后由服务回退选中列表下一条(无显式写点)。
    fireEvent.click(screen.getByRole('button', { name: '删除会话' }));
    await waitFor(() => expect(store.getState().selectedSessionId).toBe('次会话'));
    expect(screen.getByTestId('chat-session')).toHaveTextContent('次会话');

    const filteredLoad = deferred<Awaited<ReturnType<typeof conversationService.listManagedSessions>>>();
    jest.mocked(conversationService.listManagedSessions).mockReturnValueOnce(filteredLoad.promise);
    fireEvent.click(screen.getByRole('button', { name: '搜索未命中' }));
    await act(async () => {
      filteredLoad.resolve({ ok: true, data: { items: [s('zz9', 'a')], total: 1 } });
      await filteredLoad.promise;
    });
    expect(screen.getByTestId('chat-session')).toHaveTextContent('次会话');
    expect(screen.queryByText('Bot A 暂无会话')).not.toBeInTheDocument();
  });
});

describe('ConversationSessionPlaceholder 一级导航内联入口', () => {
  it('壳内渲染：占位顶行一级导航入口与「打开会话列表」同排且在其左侧（28px、<lg）', () => {
    const primaryNavApi = {
      openNav: jest.fn(),
      mountNavEntry: jest.fn(),
      unmountNavEntry: jest.fn(),
    };
    const model = {
      botName: 'Bot B',
      status: 'empty' as const,
      error: undefined,
      creating: false,
      create: jest.fn(async () => undefined),
      retry: jest.fn(),
      showAll: jest.fn(),
    };
    render(
      <PrimaryNavContext.Provider value={primaryNavApi}>
        <ConversationSessionPlaceholder model={model} onOpenSessionList={onOpenList} />
      </PrimaryNavContext.Provider>,
    );

    const navEntry = screen.getByRole('button', { name: '打开导航' });
    const sessionListEntry = screen.getByRole('button', { name: '打开会话列表' });
    expect(navEntry).toHaveClass('h-7', 'w-7', 'lg:hidden');
    // 占位顶行两钮同排（同一 flex 行内 DOM 顺序）。
    expect(navEntry.compareDocumentPosition(sessionListEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(navEntry);
    expect(primaryNavApi.openNav).toHaveBeenCalledTimes(1);
  });
});
