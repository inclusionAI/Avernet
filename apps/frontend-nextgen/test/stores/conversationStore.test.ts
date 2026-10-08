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

describe('conversationStore', () => {
  beforeEach(() => useConversationStore.getState().reset());

  it('allows multiple managed Bots to stay expanded with independent filters', () => {
    const store = useConversationStore.getState();
    store.setExpandedBot('bot-a:1', true);
    store.setExpandedBot('bot-b:2', true);
    store.setManagedBotOrigin('bot-a:1', 'others');
    store.setManagedBotScope('bot-b:2', 'favorite');

    expect(useConversationStore.getState().expandedBotIds).toEqual({ 'bot-a:1': true, 'bot-b:2': true });
    expect(useConversationStore.getState().originByManagedBotId['bot-a:1']).toBe('others');
    expect(useConversationStore.getState().scopeByManagedBotId['bot-b:2']).toBe('favorite');
  });

  it('collapses a Bot by removing its expansion key', () => {
    const store = useConversationStore.getState();
    store.setExpandedBot('bot-a:1', true);
    store.setExpandedBot('bot-a:1', false);
    expect(useConversationStore.getState().expandedBotIds).toEqual({});
  });

  it('others switches effective scope to all without erasing remembered mine scope', () => {
    const store = useConversationStore.getState();
    store.setManagedBotScope('bot-a:1', 'favorite');
    expect(useConversationStore.getState().effectiveScopeByManagedBotId['bot-a:1']).toBe('favorite');

    store.setManagedBotOrigin('bot-a:1', 'others');
    expect(useConversationStore.getState().originByManagedBotId['bot-a:1']).toBe('others');
    expect(useConversationStore.getState().effectiveScopeByManagedBotId['bot-a:1']).toBe('all');
    // 记忆的 mine 范围不被覆盖。
    expect(useConversationStore.getState().scopeByManagedBotId['bot-a:1']).toBe('favorite');

    // 切回 mine 恢复记忆范围。
    store.setManagedBotOrigin('bot-a:1', 'mine');
    expect(useConversationStore.getState().effectiveScopeByManagedBotId['bot-a:1']).toBe('favorite');
  });

  it('origin switch for a Bot clears its selected Session only', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's1',
    });
    store.setManagedBotOrigin('bot-a:1', 'others');
    expect(useConversationStore.getState().selectedSessionId).toBeNull();
    expect(useConversationStore.getState().selectedBotId).toBe('bot-a:1');
  });

  it('origin switch for another Bot does not clear current selection', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's1',
    });
    store.setManagedBotOrigin('bot-b:2', 'others');
    expect(useConversationStore.getState().selectedSessionId).toBe('s1');
  });

  it('origin switch of the selected Bot syncs the selection to the new origin (AC-6/AC-7)', () => {
    const store = useConversationStore.getState();
    store.setManagedBotScope('bot-a:1', 'favorite');
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'favorite',
      friendUserId: null,
      sessionId: 's1',
    });

    // mine → others:选中归属跟随,范围回落为生效范围(others 强制 all)。
    store.setManagedBotOrigin('bot-a:1', 'others');
    let s = useConversationStore.getState();
    expect(s.selectedOrigin).toBe('others');
    expect(s.selectedScope).toBe('all');
    expect(s.selectedSessionId).toBeNull();

    // others → mine:选中归属与生效范围(记忆 favorite)跟随。
    store.setManagedBotOrigin('bot-a:1', 'mine');
    s = useConversationStore.getState();
    expect(s.selectedOrigin).toBe('mine');
    expect(s.selectedScope).toBe('favorite');
  });

  it('origin switch of the selected Bot to others clears the friend selection', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'others',
      scope: 'all',
      friendUserId: 'f9',
      sessionId: null,
    });

    store.setManagedBotOrigin('bot-a:1', 'mine');
    expect(useConversationStore.getState().selectedFriendUserId).toBeNull();
  });

  it('scope switch of the selected managed Bot syncs the selection scope while mine', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: null,
    });

    store.setManagedBotScope('bot-a:1', 'favorite');
    expect(useConversationStore.getState().selectedScope).toBe('favorite');
    // 非选中 Bot 的范围切换不触碰选中态。
    store.setManagedBotScope('bot-b:2', 'favorite');
    expect(useConversationStore.getState().selectedScope).toBe('favorite');
    expect(useConversationStore.getState().selectedBotId).toBe('bot-a:1');
  });

  it('filter changes of a non-selected Bot do not touch the selection', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'others',
      scope: 'all',
      friendUserId: 'f9',
      sessionId: null,
    });

    store.setManagedBotOrigin('bot-b:2', 'others');
    store.setManagedBotOrigin('bot-b:2', 'mine');
    store.setManagedBotScope('bot-b:2', 'favorite');

    const s = useConversationStore.getState();
    expect(s.selectedBotId).toBe('bot-a:1');
    expect(s.selectedOrigin).toBe('others');
    expect(s.selectedScope).toBe('all');
    expect(s.selectedFriendUserId).toBe('f9');
  });

  it('tracks friend group expansion per Bot independently', () => {
    const store = useConversationStore.getState();
    store.setExpandedFriend('bot-a:1', '447147', true);
    store.setExpandedFriend('bot-a:1', '123456', true);
    store.setExpandedFriend('bot-b:2', '447147', false);

    expect(useConversationStore.getState().expandedFriendUserIdsByBotId).toEqual({
      'bot-a:1': { '447147': true, '123456': true },
      'bot-b:2': {},
    });
  });

  it('writes managed Bot cache per Bot without touching others', () => {
    const store = useConversationStore.getState();
    store.setManagedBotCache('bot-a:1', managedView('bot-a:1', { origin: 'others' }));
    store.setManagedBotCache('bot-b:2', managedView('bot-b:2'));

    expect(useConversationStore.getState().sessionsByBotId['bot-a:1'].origin).toBe('others');
    expect(useConversationStore.getState().sessionsByBotId['bot-b:2'].origin).toBe('mine');

    store.setManagedBotCache('bot-a:1', managedView('bot-a:1', { scope: 'favorite' }));
    expect(useConversationStore.getState().sessionsByBotId['bot-a:1'].scope).toBe('favorite');
    expect(useConversationStore.getState().sessionsByBotId['bot-a:1'].origin).toBe('mine');
  });

  it('writes friend Bot sessions cache per Bot', () => {
    const store = useConversationStore.getState();
    store.setFriendBotSessions('friend-bot:1', sessionList({ page: 2, total: 12 }));
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend-bot:1'].total).toBe(12);
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend-bot:1'].page).toBe(2);
  });

  it('selectConversation records the full main-stage selection', () => {
    const store = useConversationStore.getState();
    store.selectConversation({
      botId: 'bot-a:2088',
      section: 'managed',
      origin: 'others',
      scope: 'all',
      friendUserId: '447147',
      sessionId: 's9',
    });
    const s = useConversationStore.getState();
    expect(s.selectedBotId).toBe('bot-a:2088');
    expect(s.selectedSection).toBe('managed');
    expect(s.selectedOrigin).toBe('others');
    expect(s.selectedScope).toBe('all');
    expect(s.selectedFriendUserId).toBe('447147');
    expect(s.selectedSessionId).toBe('s9');
  });

  it('reset restores the initial state', () => {
    const store = useConversationStore.getState();
    store.setExpandedBot('bot-a:1', true);
    store.setManagedBotScope('bot-a:1', 'favorite');
    store.setManagedBotOrigin('bot-a:1', 'others');
    store.setExpandedFriend('bot-a:1', '447147', true);
    store.selectConversation({
      botId: 'bot-a:1',
      section: 'managed',
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: 's1',
    });
    store.setManagedBotCache('bot-a:1', managedView('bot-a:1'));
    store.setFriendBotSessions('friend-bot:1', sessionList());

    useConversationStore.getState().reset();

    const s = useConversationStore.getState();
    expect(s.expandedBotIds).toEqual({});
    expect(s.originByManagedBotId).toEqual({});
    expect(s.scopeByManagedBotId).toEqual({});
    expect(s.effectiveScopeByManagedBotId).toEqual({});
    expect(s.expandedFriendUserIdsByBotId).toEqual({});
    expect(s.selectedBotId).toBeNull();
    expect(s.selectedSection).toBeNull();
    expect(s.selectedOrigin).toBe('mine');
    expect(s.selectedScope).toBe('all');
    expect(s.selectedFriendUserId).toBeNull();
    expect(s.selectedSessionId).toBeNull();
    expect(s.sessionsByBotId).toEqual({});
    expect(s.friendBotSessionsByBotId).toEqual({});
  });
});
