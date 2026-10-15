// 会话侧栏 sessionKey 搜索与好友 Bot 范围的 Store 同步契约。
// 设计依据:specs/2026-10-14-conversation-session-key-search/spec.md §数据流(Store 只存同步筛选状态)。
import type { ConversationSessionListState, ManagedBotConversationView } from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

function sessionList(partial: Partial<ConversationSessionListState> = {}): ConversationSessionListState {
  return {
    items: [],
    page: 1,
    total: 0,
    hasMore: false,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
    ...partial,
  };
}

function managedView(botId: string, partial: Partial<ManagedBotConversationView> = {}): ManagedBotConversationView {
  return {
    botId,
    origin: 'mine',
    scope: 'all',
    sessions: sessionList(),
    friendDirectory: { items: [], loading: false, error: null },
    friendGroups: {},
    ...partial,
  };
}

function revisionOf(botId: string): number {
  return useConversationStore.getState().sessionFilterRevisionByBotId[botId] ?? 0;
}

describe('conversationStore session filters', () => {
  beforeEach(() => useConversationStore.getState().reset());

  it('stores trimmed session keys per Bot independently', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('bot-a:1', ' agent:main:sn-1 ');
    store.setSessionKeyFilter('bot-b:2', 'agent:main:sn-2');

    expect(useConversationStore.getState().sessionKeyByBotId).toEqual({
      'bot-a:1': 'agent:main:sn-1',
      'bot-b:2': 'agent:main:sn-2',
    });
  });

  it('submitting an empty search clears the stored key entry', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');
    store.setSessionKeyFilter('bot-a:1', '   ');

    expect(useConversationStore.getState().sessionKeyByBotId['bot-a:1'] ?? '').toBe('');
  });

  it('switching a managed Bot to favorite scope clears its search and coming back does not restore it', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');
    store.setManagedBotScope('bot-a:1', 'favorite');
    expect(useConversationStore.getState().sessionKeyByBotId['bot-a:1'] ?? '').toBe('');

    store.setManagedBotScope('bot-a:1', 'all');
    expect(useConversationStore.getState().sessionKeyByBotId['bot-a:1'] ?? '').toBe('');
  });

  it('switching origin back into the remembered favorite scope clears the search', () => {
    const store = useConversationStore.getState();
    store.setManagedBotScope('bot-a:1', 'favorite');
    store.setManagedBotOrigin('bot-a:1', 'others');
    // others 强制 all:搜索入口有效,允许输入新条件。
    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');
    expect(useConversationStore.getState().sessionKeyByBotId['bot-a:1']).toBe('agent:main:sn-1');

    store.setManagedBotOrigin('bot-a:1', 'mine');
    expect(useConversationStore.getState().sessionKeyByBotId['bot-a:1'] ?? '').toBe('');
    expect(useConversationStore.getState().effectiveScopeByManagedBotId['bot-a:1']).toBe('favorite');
  });

  it('stores friend Bot scopes per Bot with a default of all', () => {
    const store = useConversationStore.getState();
    store.setFriendBotScope('friend:9', 'favorite');

    expect(useConversationStore.getState().scopeByFriendBotId['friend:9']).toBe('favorite');
    expect(useConversationStore.getState().scopeByFriendBotId['friend:8'] ?? 'all').toBe('all');
  });

  it('friend scope favorite clears the search and switching back to all does not restore it', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('friend:9', ' agent:main:xxx ');
    expect(useConversationStore.getState().sessionKeyByBotId['friend:9']).toBe('agent:main:xxx');
    const beforeFavoriteRevision = revisionOf('friend:9');

    store.setFriendBotScope('friend:9', 'favorite');
    expect(useConversationStore.getState().sessionKeyByBotId['friend:9'] ?? '').toBe('');
    // 切入 favorite = 一次实际过滤变化,revision 单步递增。
    expect(revisionOf('friend:9')).toBe(beforeFavoriteRevision + 1);

    store.setFriendBotScope('friend:9', 'all');
    expect(useConversationStore.getState().scopeByFriendBotId['friend:9']).toBe('all');
    expect(useConversationStore.getState().sessionKeyByBotId['friend:9'] ?? '').toBe('');
  });

  it('increments the revision per actual key change including A→B→A', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('bot-a:1', 'agent:main:A');
    const firstA = revisionOf('bot-a:1');
    expect(firstA).toBe(1);

    store.setSessionKeyFilter('bot-a:1', 'agent:main:B');
    expect(revisionOf('bot-a:1')).toBeGreaterThan(firstA);

    // A→B→A:key 回到旧值,但 revision 必须高于首提,旧 A 响应不能凭内容相等写回。
    store.setSessionKeyFilter('bot-a:1', 'agent:main:A');
    expect(revisionOf('bot-a:1')).toBeGreaterThan(firstA);
    // 其他 Bot 的修订号不受牵连。
    expect(useConversationStore.getState().sessionFilterRevisionByBotId['bot-b:2']).toBeUndefined();
  });

  it('increments the revision on managed origin and scope changes only when a value actually changes', () => {
    const store = useConversationStore.getState();

    store.setManagedBotOrigin('bot-a:1', 'others');
    expect(revisionOf('bot-a:1')).toBe(1);
    store.setManagedBotOrigin('bot-a:1', 'others');
    expect(revisionOf('bot-a:1')).toBe(1);

    store.setManagedBotOrigin('bot-a:1', 'mine');
    expect(revisionOf('bot-a:1')).toBe(2);

    store.setManagedBotScope('bot-a:1', 'favorite');
    expect(revisionOf('bot-a:1')).toBe(3);
    store.setManagedBotScope('bot-a:1', 'favorite');
    expect(revisionOf('bot-a:1')).toBe(3);

    store.setManagedBotScope('bot-a:1', 'all');
    expect(revisionOf('bot-a:1')).toBe(4);

    // 缺省 all/mine 的等值提交:无实际变化,不递增也不落记录。
    store.setManagedBotScope('bot-c:3', 'all');
    expect(useConversationStore.getState().sessionFilterRevisionByBotId['bot-c:3']).toBeUndefined();
  });

  it('no-op setter calls keep the state identical', () => {
    const store = useConversationStore.getState();
    const initial = useConversationStore.getState();

    // 空 key / 缺省范围(all)是兼容 no-op:状态引用不变。
    store.setSessionKeyFilter('bot-a:1', '');
    expect(useConversationStore.getState()).toBe(initial);
    store.setFriendBotScope('friend:9', 'all');
    expect(useConversationStore.getState()).toBe(initial);

    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');
    store.setFriendBotScope('friend:9', 'favorite');
    const applied = useConversationStore.getState();

    // 已生效条件上的等值重复提交不产生新状态。
    store.setSessionKeyFilter('bot-a:1', ' agent:main:sn-1 ');
    store.setFriendBotScope('friend:9', 'favorite');
    const repeated = useConversationStore.getState();
    expect(repeated.sessionKeyByBotId).toBe(applied.sessionKeyByBotId);
    expect(repeated.scopeByFriendBotId).toBe(applied.scopeByFriendBotId);
    expect(repeated.sessionFilterRevisionByBotId).toBe(applied.sessionFilterRevisionByBotId);
  });

  it('search changes invalidate the Bot caches without clearing the selected main conversation', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's1',
    });
    store.setManagedBotCache('bot-a:1', managedView('bot-a:1', { sessions: sessionList({ total: 3 }) }));
    store.setFriendBotSessions('friend:9', sessionList({ total: 7 }));

    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');

    // 管理 Bot 缓存失效;其他 Bot 的好友缓存与主会话选中保持不变。
    expect(useConversationStore.getState().sessionsByBotId['bot-a:1']).toBeUndefined();
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend:9']).toEqual(
      sessionList({ total: 7 }),
    );
    expect(useConversationStore.getState().selectedBotId).toBe('bot-a:1');
    expect(useConversationStore.getState().selectedSessionId).toBe('s1');

    store.setSessionKeyFilter('friend:9', 'agent:main:sn-2');
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend:9']).toBeUndefined();
  });

  it('search change on an others-origin Bot invalidates the catalog but preserves expanded friend intent', () => {
    const store = useConversationStore.getState();
    store.setManagedBotOrigin('bot-a:1', 'others');
    store.setExpandedFriend('bot-a:1', '447147', true);
    store.setManagedBotCache('bot-a:1', managedView('bot-a:1', { origin: 'others' }));

    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');

    expect(useConversationStore.getState().sessionsByBotId['bot-a:1']).toBeUndefined();
    // 展开意图保留:好友用户分组按新条件重新加载,不折叠。
    expect(useConversationStore.getState().expandedFriendUserIdsByBotId['bot-a:1']).toEqual({ '447147': true });
  });

  it('friend scope changes invalidate only that friend Bot list cache', () => {
    const store = useConversationStore.getState();
    store.setFriendBotSessions('friend:9', sessionList({ total: 5 }));
    store.setFriendBotSessions('friend:8', sessionList({ total: 6 }));

    store.setFriendBotScope('friend:9', 'favorite');

    expect(useConversationStore.getState().friendBotSessionsByBotId['friend:9']).toBeUndefined();
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend:8']).toEqual(sessionList({ total: 6 }));
  });

  it('reset clears session keys, friend scopes and filter revisions', () => {
    const store = useConversationStore.getState();
    store.setSessionKeyFilter('bot-a:1', 'agent:main:sn-1');
    store.setFriendBotScope('friend:9', 'favorite');
    store.setFriendBotSessions('friend:9', sessionList());

    useConversationStore.getState().reset();

    const s = useConversationStore.getState();
    expect(s.sessionKeyByBotId).toEqual({});
    expect(s.scopeByFriendBotId).toEqual({});
    expect(s.sessionFilterRevisionByBotId).toEqual({});
    expect(s.friendBotSessionsByBotId).toEqual({});
  });
});