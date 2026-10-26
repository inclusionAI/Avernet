/** @jest-environment jsdom */
// sessionKey 搜索与好友 Bot 会话范围的侧栏装配契约(spec 2026-10-14 session-key-search):
// - 三类目录(我的/团队/好友)行内均有搜索入口,且仅 effective scope=all 时展示;
// - 行内提交写 Store 过滤(sessionKeyByBotId)并展开该 Bot,不触动主选中;
// - 好友行只暴露「会话:全部/已收藏」,选择写 scopeByFriendBotId;favorite 清空并隐藏搜索,
//   切回 all 不恢复,主会话选中不受影响;TEClaw 好友无收藏能力不渲染筛选。
// 页面契约:Hook 订阅 Store 并回传新快照;用 rerender + 实时快照模拟(同 ConversationSidebar.test)。
import type { ConversationBotView } from '@/domain/conversation/types';
import type { ConversationSidebarProps } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import { ConversationSidebar } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import { useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, within } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: '327325', displayName: '张三', online: true },
    status: 'ready',
  }),
}));

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  useConversationStore.getState().reset();
});

const botOf = (botId: string, displayName: string, section: ConversationBotView['section']): ConversationBotView => {
  const idx = botId.indexOf(':');
  return {
    bot: {
      botId,
      realBotId: idx < 0 ? botId : botId.slice(0, idx),
      ownerId: idx < 0 ? undefined : botId.slice(idx + 1),
      displayName,
      online: true,
      chatable: true,
      isFriendBot: section === 'friend',
    },
    section,
  };
};

const managedBot = botOf('bot-a:2088', '管理 Bot A', 'managed');
const teamBot = botOf('shared:entity', '团队助手', 'team');
const friendBot = botOf('fb-1:327325', '朋友 Bot', 'friend');

const emptyList = () => ({
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
});

const directoryModel = (over: Partial<ConversationDirectoryModel> = {}): ConversationDirectoryModel => ({
  teamBots: [],
  teamLoading: false,
  teamError: null,
  retryTeam: jest.fn(),
  managedBots: [],
  friendBots: [],
  managedLoading: false,
  friendLoading: false,
  managedError: null,
  friendError: null,
  retryManaged: jest.fn(),
  retryFriend: jest.fn(),
  ...over,
});

const sessionsModel = (over: Partial<ConversationSessionsModel> = {}): ConversationSessionsModel => ({
  actions: { run: jest.fn(async () => true), isPending: jest.fn(() => false) },
  favorites: { toggleFavorite: jest.fn(async () => true), isPending: jest.fn(() => false) },
  openBotIds: {},
  toggleBot: jest.fn(),
  selectMineSession: jest.fn(),
  selectFriendBotSession: jest.fn(),
  createSession: jest.fn(),
  retrySessions: jest.fn(),
  loadMoreSessions: jest.fn(),
  ...over,
});

const othersModel = (over: Partial<ManagedBotOthersModel> = {}): ManagedBotOthersModel => ({
  toggleFriend: jest.fn(),
  retryDirectory: jest.fn(),
  retryFriendSessions: jest.fn(),
  loadMoreFriendSessions: jest.fn(),
  ...over,
});

function sidebarProps(over: Partial<ConversationSidebarProps> = {}): ConversationSidebarProps {
  return {
    teamBots: [],
    managedBots: [],
    friendBots: [],
    store: useConversationStore.getState(),
    directory: directoryModel(),
    sessions: sessionsModel(),
    others: othersModel(),
    onOpenSession: jest.fn(),
    onOpenPublicBots: jest.fn(),
    onOpenBotWorkshop: jest.fn(),
    onOpenAgentCodingBot: jest.fn(),
    replyingSessionId: null,
    ...over,
  };
}

/** 渲染并返回 syncStore:任一 Store 写入后调用一次,模拟页面订阅回传新快照。 */
function renderSidebar(over: Partial<ConversationSidebarProps> = {}) {
  const base = sidebarProps(over);
  const utils = render(<ConversationSidebar {...base} />);
  const syncStore = () => utils.rerender(<ConversationSidebar {...base} store={useConversationStore.getState()} />);
  return { ...utils, syncStore };
}

/** Bot 行内搜索入口的作用域查询:仅在该行的操作区内找搜索按钮。 */
const searchEntryOf = (rowName: string) => {
  const row = screen.getByRole('button', { name: rowName }).closest('div');
  return within(row as HTMLElement).getByRole('button', { name: '会话ID检索' });
};

describe('sessionKey 搜索入口(三类目录装配)', () => {
  it('我的/团队/好友 Bot 行都在 all 有效范围下渲染搜索入口', () => {
    renderSidebar({
      managedBots: [managedBot],
      teamBots: [teamBot],
      friendBots: [friendBot],
      directory: directoryModel({ managedBots: [managedBot], teamBots: [teamBot], friendBots: [friendBot] }),
    });
    for (const name of ['管理 Bot A', '团队助手', '朋友 Bot']) {
      expect(searchEntryOf(name)).toBeInTheDocument();
    }
  });

  it('行内提交写入该 Bot 的过滤条件并展开,不改动主选中', () => {
    const { syncStore } = renderSidebar({ friendBots: [friendBot] });
    // 点 🔍 随 onToggle 展开该 Bot;mock toggleBot 不落 store,先预置展开态。
    useConversationStore.getState().setExpandedBot(friendBot.bot.botId, true);
    syncStore();
    fireEvent.click(searchEntryOf('朋友 Bot'));
    const input = screen.getByPlaceholderText('输入sessionID回车检索会话') as HTMLInputElement;
    fireEvent.change(input, { target: { value: ' agent:main:sn-1 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    const store = useConversationStore.getState();
    expect(store.sessionKeyByBotId[friendBot.bot.botId]).toBe('agent:main:sn-1');
    expect(store.expandedBotIds[friendBot.bot.botId]).toBe(true);
    // 提交只落过滤+展开,不制造主舞台选中。
    expect(store.selectedBotId).toBeNull();
    expect(store.selectedSection).toBeNull();
    expect(store.selectedSessionId).toBeNull();
    syncStore();
    // 生效后:🔍 按钮主色高亮(激活态圆点),且 Bot 展开可见。
    expect(searchEntryOf('朋友 Bot')).toHaveClass('text-primary');
  });
});

describe('好友 Bot 会话范围筛选(装配)', () => {
  it('好友行渲染仅会话范围的筛选;TEClaw 好友不渲染筛选入口', () => {
    const teclawFriend = botOf('fb-2:327325', 'TEClaw 好友', 'friend');
    teclawFriend.bot.engine = 'TEClaw';
    renderSidebar({
      friendBots: [friendBot, teclawFriend],
      directory: directoryModel({ friendBots: [friendBot, teclawFriend] }),
    });
    expect(screen.getByRole('button', { name: '会话筛选' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发起人与会话筛选' })).not.toBeInTheDocument();
    const teclawRow = screen.getByRole('button', { name: 'TEClaw 好友' }).closest('div') as HTMLElement;
    expect(within(teclawRow).queryByRole('button', { name: '会话筛选' })).not.toBeInTheDocument();
  });

  it('选择已收藏写好友范围、保留主选中;加载更多携带好友实际范围', () => {
    const botId = friendBot.bot.botId;
    const sessions = sessionsModel();
    const { syncStore } = renderSidebar({
      friendBots: [friendBot],
      directory: directoryModel({ friendBots: [friendBot] }),
      sessions,
    });
    const store = useConversationStore.getState();
    store.setExpandedBot(botId, true);
    store.setFriendBotSessions(botId, {
      ...emptyList(),
      items: [{ sessionId: 'fs-1', botId, title: '好友会话 1', messageCount: 0, gmtCreate: '', gmtModified: '' }],
    });
    store.selectConversation({ botId, section: 'friend', origin: 'mine', scope: 'all', friendUserId: null, sessionId: 'fs-1' });
    syncStore();

    fireEvent.click(screen.getByRole('button', { name: '会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '已收藏' }));
    expect(useConversationStore.getState().scopeByFriendBotId[botId]).toBe('favorite');
    // 好友范围切换不投影主选中:选中会话保持。
    const selected = useConversationStore.getState();
    expect(selected.selectedBotId).toBe(botId);
    expect(selected.selectedSessionId).toBe('fs-1');
    syncStore();

    // favorite 生效中:筛选按钮主色高亮,搜索入口隐藏。
    expect(screen.getByRole('button', { name: '会话筛选' })).toHaveClass('text-primary');
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();

    // 收藏列表存在 hasMore 时,加载更多回调用的是好友实际范围。
    useConversationStore.getState().setFriendBotSessions(botId, {
      ...emptyList(),
      items: [
        { sessionId: 'fs-9', botId, title: '好友收藏', messageCount: 0, gmtCreate: '', gmtModified: '', favorite: true },
      ],
      page: 1,
      total: 11,
      hasMore: true,
    });
    syncStore();
    fireEvent.click(screen.getByRole('button', { name: '加载更多会话' }));
    expect(sessions.loadMoreSessions).toHaveBeenCalledWith(botId, 'friend', 'favorite');
  });

  it('切到已收藏清空且隐藏已生效搜索;切回全部不恢复条件', () => {
    const botId = friendBot.bot.botId;
    const { syncStore } = renderSidebar({
      friendBots: [friendBot],
      directory: directoryModel({ friendBots: [friendBot] }),
    });
    // 先通过搜索 UI 生效条件(favorite 场景无需先展示列表)。
    useConversationStore.getState().setExpandedBot(botId, true);
    syncStore();
    fireEvent.click(searchEntryOf('朋友 Bot'));
    fireEvent.change(screen.getByPlaceholderText('输入sessionID回车检索会话'), { target: { value: 'agent:main:mark' } });
    fireEvent.keyDown(screen.getByPlaceholderText('输入sessionID回车检索会话'), { key: 'Enter' });
    expect(useConversationStore.getState().sessionKeyByBotId[botId]).toBe('agent:main:mark');
    syncStore();

    fireEvent.click(screen.getByRole('button', { name: '会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '已收藏' }));
    // Store 已随切换清空(sessionKeyByBotId),切回 all 不恢复。
    expect(useConversationStore.getState().sessionKeyByBotId[botId]).toBeUndefined();
    syncStore();
    // favorite 下搜索入口隐藏。
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '全部' }));
    expect(useConversationStore.getState().scopeByFriendBotId[botId]).toBe('all');
    expect(useConversationStore.getState().sessionKeyByBotId[botId]).toBeUndefined();
    syncStore();
    // 切回 all:搜索按钮回归默认态(无高亮),输入框恢复常驻且草稿回到已生效条件(空)。
    const entry = searchEntryOf('朋友 Bot');
    expect(entry).not.toHaveClass('text-primary');
    expect((screen.getByRole('textbox', { name: '会话ID检索' }) as HTMLInputElement).value).toBe('');
  });
});

describe('管理 Bot 搜索与既有筛选共用(装配)', () => {
  it('管理 Bot 切换 favorite 后隐藏搜索,others 视角沿用 all 仍展示搜索', () => {
    const botId = managedBot.bot.botId;
    const { syncStore } = renderSidebar({
      managedBots: [managedBot],
      directory: directoryModel({ managedBots: [managedBot] }),
    });
    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '已收藏' }));
    expect(useConversationStore.getState().scopeByManagedBotId[botId]).toBe('favorite');
    syncStore();
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '他人发起' }));
    expect(useConversationStore.getState().originByManagedBotId[botId]).toBe('others');
    syncStore();
    // origin=others 强制 all:搜索入口保留,驱动他人会话分组过滤。
    expect(searchEntryOf('管理 Bot A')).toBeInTheDocument();
  });

  it('管理 Bot 行内提交搜索只落过滤与展开,不改归属/范围', () => {
    const botId = managedBot.bot.botId;
    const store = useConversationStore.getState();
    store.selectConversation({ botId, section: 'managed', origin: 'mine', scope: 'all', friendUserId: null, sessionId: 's-0' });
    store.setExpandedBot(botId, true);
    const { syncStore } = renderSidebar({ managedBots: [managedBot], directory: directoryModel({ managedBots: [managedBot] }) });
    syncStore();
    fireEvent.click(searchEntryOf('管理 Bot A'));
    fireEvent.change(screen.getByPlaceholderText('输入sessionID回车检索会话'), { target: { value: 'agent:main:managed-key' } });
    fireEvent.keyDown(screen.getByPlaceholderText('输入sessionID回车检索会话'), { key: 'Enter' });
    const next = useConversationStore.getState();
    expect(next.sessionKeyByBotId[botId]).toBe('agent:main:managed-key');
    expect(next.expandedBotIds[botId]).toBe(true);
    expect(next.originByManagedBotId[botId]).toBeUndefined();
    expect(next.scopeByManagedBotId[botId]).toBeUndefined();
    // 已有主选中保持(搜索只过滤列表)。
    expect(next.selectedBotId).toBe(botId);
    expect(next.selectedSessionId).toBe('s-0');
  });
});
