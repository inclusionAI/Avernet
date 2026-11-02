import { parseConversationRoute, serializeConversationRoute } from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';

beforeEach(() => useConversationStore.getState().reset());
test.each([
  'section=team&bot=shared%3Aentity&origin=mine&scope=favorite&session=s1',
  'section=team&bot=shared%3Aentity&origin=others&friend=reader&session=s2',
])('team route roundtrip %s', (url) => {
  expect(parseConversationRoute(url).section).toBe('team');
  expect(serializeConversationRoute(parseConversationRoute(url))).toBe(url);
});
test('team origin/scope 与管理 Bot 同步选中状态，来源切换清会话', () => {
  const store = useConversationStore.getState();
  store.selectConversation({
    section: 'team',
    botId: 'shared:entity',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: 's1',
  });
  store.setManagedBotScope('shared:entity', 'favorite');
  expect(useConversationStore.getState().selectedScope).toBe('favorite');
  store.setManagedBotOrigin('shared:entity', 'others');
  expect(useConversationStore.getState()).toMatchObject({
    selectedOrigin: 'others',
    selectedSessionId: null,
    selectedScope: 'all',
  });
  store.setManagedBotOrigin('shared:entity', 'mine');
  expect(useConversationStore.getState().selectedScope).toBe('favorite');
});
test('team 展开跨分类单展开，且不抢占既有主舞台选中与已记忆的 others 归属', () => {
  const store = useConversationStore.getState();
  store.selectConversation({
    section: 'managed',
    botId: 'owned:owner',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: 's1',
  });
  store.setExpandedBot('owned:owner', true);
  store.setManagedBotOrigin('shared:entity', 'others');
  // 展开动作只改 expandedBotIds：手风琴收起旧列表，主舞台选中与来源记忆保持不变。
  store.setExpandedBot('shared:entity', true);
  const after = useConversationStore.getState();
  expect(after.expandedBotIds).toEqual({ 'shared:entity': true });
  expect(after.originByManagedBotId['shared:entity']).toBe('others');
  expect(after).toMatchObject({
    selectedBotId: 'owned:owner',
    selectedSection: 'managed',
    selectedSessionId: 's1',
  });
});
