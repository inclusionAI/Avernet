/** @jest-environment jsdom */
// useConversationSelection 直测:入向 URL → Store 解析(展开/归属/范围/好友/单选)、
// 目录到达(hydrated)后未决 section 的自动重解析、出向 ConversationRouteState 投影、
// mine(交互式)/others(只读)视图数据推导。
import {
  serializeConversationRoute,
  type ConversationBotView,
  type ConversationRouteState,
} from '@/domain/conversation';
import {
  useConversationSelection,
  type UseConversationSelectionResult,
} from '@/pages/Workspace/Chat/hooks/useConversationSelection';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { conversationInitialState, useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

const managedBot: ChatBotView = {
  botId: 'bot-a:1',
  realBotId: 'bot-a',
  ownerId: '1',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};
const friendBot: ChatBotView = {
  botId: 'friend:2',
  realBotId: 'friend',
  ownerId: '2',
  displayName: '好友 Bot',
  online: true,
  chatable: true,
  isFriendBot: true,
};
const managedView: ConversationBotView = { bot: managedBot, section: 'managed' };
const friendView: ConversationBotView = { bot: friendBot, section: 'friend' };
const emptyList = {
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

interface SelectionHookProps {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  hydrated: boolean;
}

function seedStore(patch: Partial<ConversationState>) {
  act(() => {
    // 合并进当前态(不清掉既有选中),与页内 Store 变更语义一致。
    useConversationStore.setState((current) => ({ ...current, ...patch }));
  });
}

beforeEach(() => {
  useConversationStore.setState({ ...conversationInitialState });
});

describe('useConversationSelection 入向(URL → Store)', () => {
  it('friend 深链:展开 Bot、origin=mine、单选会话', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [friendView], hydrated: false }),
    );

    act(() => result.current.onRouteSelection({ section: 'friend', botId: 'friend:2', sessionId: 's2' }));

    const store = useConversationStore.getState();
    expect(store.expandedBotIds['friend:2']).toBe(true);
    expect(store.selectedBotId).toBe('friend:2');
    expect(store.selectedSection).toBe('friend');
    expect(store.selectedOrigin).toBe('mine');
    expect(store.selectedScope).toBe('all');
    expect(store.selectedFriendUserId).toBeNull();
    expect(store.selectedSessionId).toBe('s2');
  });

  it('managed/mine + scope=favorite:记忆归属与范围并单选', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );

    act(() =>
      result.current.onRouteSelection({
        section: 'managed',
        botId: 'bot-a:1',
        origin: 'mine',
        scope: 'favorite',
        sessionId: 's1',
      }),
    );

    const store = useConversationStore.getState();
    expect(store.expandedBotIds['bot-a:1']).toBe(true);
    // mine 是缺省归属:记忆位保持未写(与 Sidebar 侧的 `?? 'mine'` 读取一致)。
    expect(store.originByManagedBotId['bot-a:1'] ?? 'mine').toBe('mine');
    expect(store.effectiveScopeByManagedBotId['bot-a:1']).toBe('favorite');
    expect(store.selectedOrigin).toBe('mine');
    expect(store.selectedScope).toBe('favorite');
    expect(store.selectedSessionId).toBe('s1');
  });

  it('managed/others 深链:置归属、展开好友分组、选中只读会话', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );

    act(() =>
      result.current.onRouteSelection({
        section: 'managed',
        botId: 'bot-a:1',
        origin: 'others',
        friendUserId: 'f9',
        sessionId: 'os1',
      }),
    );

    const store = useConversationStore.getState();
    expect(store.originByManagedBotId['bot-a:1']).toBe('others');
    expect(store.effectiveScopeByManagedBotId['bot-a:1']).toBe('all');
    expect(store.expandedFriendUserIdsByBotId['bot-a:1']?.f9).toBe(true);
    expect(store.selectedOrigin).toBe('others');
    expect(store.selectedFriendUserId).toBe('f9');
    expect(store.selectedSessionId).toBe('os1');
  });

  it('section 未解析的深链:目录到达后自动按目录重解析一次', () => {
    // 目录尚未就绪(hydrated=false、列表为空):解析保持未决(section=null)。
    const { result, rerender } = renderHook<UseConversationSelectionResult, SelectionHookProps>(
      (props) => useConversationSelection(props),
      { initialProps: { managedBots: [], friendBots: [], hydrated: false } },
    );

    act(() => result.current.onRouteSelection({ botId: 'bot-a:1', sessionId: 's1' }));
    expect(useConversationStore.getState().selectedSection).toBeNull();

    // 目录就绪且含该 Bot → 未决路由按目录重解析为 managed。
    rerender({ managedBots: [managedView], friendBots: [], hydrated: true });
    const store = useConversationStore.getState();
    expect(store.selectedSection).toBe('managed');
    expect(store.selectedOrigin).toBe('mine');
    expect(store.selectedSessionId).toBe('s1');
  });

  it('目录不可匹配的 bot:section 保持未解析(原样透传)', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [friendView], hydrated: true }),
    );

    act(() => result.current.onRouteSelection({ botId: 'ghost-bot', sessionId: 's9' }));

    const store = useConversationStore.getState();
    expect(store.selectedBotId).toBe('ghost-bot');
    expect(store.selectedSection).toBeNull();
  });

  it('完全空的解析(无参数 URL)不清空页内已有选中', () => {
    seedStore({
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedSessionId: 's1',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: true }),
    );

    act(() => result.current.onRouteSelection({}));

    const store = useConversationStore.getState();
    expect(store.selectedBotId).toBe('bot-a:1');
    expect(store.selectedSessionId).toBe('s1');
  });

  it('origin=others 缺 friend:剔除 sessionId,投影不回写残缺组合(AC-13)', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: true }),
    );

    act(() =>
      result.current.onRouteSelection({
        section: 'managed',
        botId: 'bot-a:1',
        origin: 'others',
        sessionId: 's9',
      }),
    );

    const store = useConversationStore.getState();
    expect(store.selectedBotId).toBe('bot-a:1');
    expect(store.selectedOrigin).toBe('others');
    expect(store.selectedSessionId).toBeNull();
    // 投影保持 origin=others 且无 session= 串。
    expect(result.current.selection).toEqual({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'others',
      scope: undefined,
      friendUserId: undefined,
      sessionId: undefined,
    });
    expect(serializeConversationRoute(result.current.selection)).toBe(
      `section=managed&bot=${encodeURIComponent('bot-a:1')}&origin=others`,
    );
  });
});

describe('useConversationSelection 出向(Store → 路由投影)', () => {
  it('managed/others 选中:带 origin=others + friend,scope 被剥离', () => {
    seedStore({
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: 'f9',
      selectedSessionId: 'os1',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );

    expect(result.current.selection).toEqual({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'others',
      scope: undefined,
      friendUserId: 'f9',
      sessionId: 'os1',
    });
    expect(result.current.origin).toBe('others');
  });

  it('friend 选中:不携带 managed-only 字段', () => {
    seedStore({
      selectedBotId: 'friend:2',
      selectedSection: 'friend',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 's2',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [], friendBots: [friendView], hydrated: false }),
    );

    expect(result.current.selection).toEqual({
      botId: 'friend:2',
      section: 'friend',
      origin: undefined,
      scope: undefined,
      friendUserId: undefined,
      sessionId: 's2',
    });
    expect(result.current.origin).toBe('mine');
  });

  it('无选中:投影空对象', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );
    const selection: ConversationRouteState = result.current.selection;
    expect(selection).toEqual({});
  });

  it('选中 Bot 的归属切换:投影跟随新归属,不残留 mine(AC-6/AC-7)', () => {
    seedStore({
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 's1',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: true }),
    );
    // 前置:mine 选中投影 origin=mine。
    expect(result.current.selection.scope).toBe('all');

    // 切到「他人发起的」续选中会话能已失效,但 origin/scope 需立即跟随投影。
    act(() => useConversationStore.getState().setManagedBotOrigin('bot-a:1', 'others'));

    expect(result.current.selection).toEqual({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'others',
      scope: undefined,
      friendUserId: undefined,
      sessionId: undefined,
    });
    expect(serializeConversationRoute(result.current.selection)).toBe(
      `section=managed&bot=${encodeURIComponent('bot-a:1')}&origin=others`,
    );
  });
});

describe('useConversationSelection 视图数据推导', () => {
  it('mine 选中:交互式推导出 Bot 与会话', () => {
    seedStore({
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedSessionId: 's1',
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'mine',
          scope: 'all',
          sessions: {
            ...emptyList,
            items: [
              {
                sessionId: 's1',
                botId: 'bot-a:1',
                title: '我的会话',
                messageCount: 2,
                gmtModified: '',
                gmtCreate: '',
              },
            ],
          },
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [friendView], hydrated: false }),
    );

    expect(result.current.interactive.bot?.botId).toBe('bot-a:1');
    expect(result.current.interactive.session?.sessionId).toBe('s1');
    expect(result.current.readonly).toEqual({ bot: null, friend: null, session: null });
  });

  it('选中会话所在的缓存归属不匹配(origin 被切到 others)时不可见', () => {
    seedStore({
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedSessionId: 's1',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );
    expect(result.current.interactive.session).toBeNull();

    seedStore({
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'others',
          scope: 'all',
          sessions: {
            ...emptyList,
            items: [
              {
                sessionId: 's1',
                botId: 'bot-a:1',
                title: '我的会话',
                messageCount: 0,
                gmtModified: '',
                gmtCreate: '',
              },
            ],
          },
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });
    // 缓存仍挂着旧 origin=mine 印记之外的others 归属:mine 读取按 origin 门禁丢弃。
    expect(result.current.interactive.session).toBeNull();
  });

  it('others 选中:只读三元组来自 others 缓存;缺缓存降级 null', () => {
    seedStore({
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'others',
          scope: 'all',
          sessions: emptyList,
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {
            f9: {
              friend: { userId: 'f9', displayName: '小明' },
              state: 'loaded',
              sessions: {
                ...emptyList,
                items: [
                  {
                    sessionId: 'os1',
                    botId: 'bot-a:1',
                    title: '他人会话',
                    messageCount: 0,
                    gmtModified: '',
                    gmtCreate: '',
                  },
                ],
              },
              expanded: true,
            },
          },
        },
      },
      selectedBotId: 'bot-a:1',
      selectedSection: 'managed',
      selectedOrigin: 'others',
      selectedFriendUserId: 'f9',
      selectedSessionId: 'os1',
    });
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );

    expect(result.current.readonly.bot?.botId).toBe('bot-a:1');
    expect(result.current.readonly.friend).toEqual({ userId: 'f9', displayName: '小明' });
    expect(result.current.readonly.session?.sessionId).toBe('os1');

    // 清掉缓存后 friend/session 降级(bot 仍来自目录)→ 页渲染「请选择」空态,不崩。
    seedStore({ sessionsByBotId: {} });
    expect(result.current.readonly).toEqual({ bot: managedBot, friend: null, session: null });
  });

  it('selectReadonlySession:展开 Bot/好友并写入 others 选中', () => {
    const { result } = renderHook(() =>
      useConversationSelection({ managedBots: [managedView], friendBots: [], hydrated: false }),
    );

    act(() => result.current.selectReadonlySession('bot-a:1', 'os1', 'f9'));

    const store = useConversationStore.getState();
    expect(store.expandedBotIds['bot-a:1']).toBe(true);
    expect(store.expandedFriendUserIdsByBotId['bot-a:1']?.f9).toBe(true);
    expect(store.selectedOrigin).toBe('others');
    expect(store.selectedFriendUserId).toBe('f9');
    expect(store.selectedSessionId).toBe('os1');
  });
});
