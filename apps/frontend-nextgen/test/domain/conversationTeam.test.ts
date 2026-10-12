import { parseConversationRoute, serializeConversationRoute } from '@/domain/conversation';
import { conversationBotSelectionService } from '@/services/workspace/conversationBotSelectionService';
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
test('team 展开沿用已记忆的 others，不误选为 mine，跨分类单展开', () => {
  const store = useConversationStore.getState();
  store.setExpandedBot('owned:owner', true);
  store.setManagedBotOrigin('shared:entity', 'others');
  const target = conversationBotSelectionService.begin({
    section: 'team',
    bot: {
      botId: 'shared:entity',
      realBotId: 'shared',
      ownerId: 'entity',
      displayName: '团队',
      online: true,
      chatable: true,
    },
  });
  expect(target).toEqual({ section: 'team', botId: 'shared:entity', origin: 'others', scope: 'all' });
  expect(useConversationStore.getState().expandedBotIds).toEqual({ 'shared:entity': true });
});
