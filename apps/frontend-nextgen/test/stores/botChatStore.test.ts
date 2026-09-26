import { useBotChatStore } from '@/stores/botChatStore';

describe('botChatStore', () => {
  afterEach(() => useBotChatStore.getState().reset());

  test('打开新 Bot 时清空旧筛选和详情', () => {
    const store = useBotChatStore.getState();
    store.setFilter('keyword', 'old');
    store.openFor({ botId: 'b1', botName: 'Bot', userId: 'u1' });
    expect(useBotChatStore.getState()).toMatchObject({
      open: true,
      filters: { keyword: '' },
      context: { botId: 'b1' },
    });
  });

  test('应用和重置筛选保持列表查询条件明确', () => {
    useBotChatStore.getState().setFilter('sessionKey', 'session-1');
    useBotChatStore.getState().applyFilters();
    expect(useBotChatStore.getState().appliedFilters.sessionKey).toBe('session-1');
    useBotChatStore.getState().clearFilters();
    expect(useBotChatStore.getState().appliedFilters.sessionKey).toBe('');
  });
});
