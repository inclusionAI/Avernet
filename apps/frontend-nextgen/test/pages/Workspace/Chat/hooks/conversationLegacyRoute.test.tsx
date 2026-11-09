/** @jest-environment jsdom */
import { resolveConversationRouteTarget } from '@/domain/conversation/resolveRouteTarget';
import type { ConversationBotView } from '@/domain/conversation';
import { useConversationSelection } from '@/pages/Workspace/Chat/hooks/useConversationSelection';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { act, renderHook } from '@testing-library/react';

const view = (section: ConversationBotView['section'], owner = '149608'): ConversationBotView => ({
  section,
  bot: { botId: `20261106_fhrmyly2:${owner}`, realBotId: '20261106_fhrmyly2', ownerId: owner,
    displayName: 'Bot', online: true, chatable: true },
});

beforeEach(() => useConversationStore.setState({ ...conversationInitialState }));

it.each(['managed', 'team', 'friend'] as const)('normalizes a cold legacy %s link after hydration', (section) => {
  const target = view(section);
  const { result, rerender } = renderHook((props: {
    managedBots: ConversationBotView[]; friendBots: ConversationBotView[]; hydrated: boolean;
  }) => useConversationSelection(props), {
    initialProps: { managedBots: [] as ConversationBotView[], friendBots: [] as ConversationBotView[], hydrated: false },
  });
  act(() => result.current.onRouteSelection({ botId: target.bot.realBotId }));
  expect(useConversationStore.getState().selectedSection).toBeNull();
  rerender({ managedBots: section === 'friend' ? [] : [target],
    friendBots: section === 'friend' ? [target] : [], hydrated: true });
  expect(result.current.selection).toMatchObject({ botId: target.bot.botId, section });
  expect(useConversationStore.getState().expandedBotIds[target.bot.botId]).toBe(true);
  expect(result.current.selection.sessionId).toBeUndefined();
});

it('retains an explicitly addressed session while canonicalizing the Bot', () => {
  const target = view('managed');
  const { result } = renderHook(() => useConversationSelection({ managedBots: [target], friendBots: [], hydrated: true }));
  act(() => result.current.onRouteSelection({ botId: target.bot.realBotId, sessionId: 'old-session' }));
  expect(result.current.selection).toMatchObject({ botId: target.bot.botId, sessionId: 'old-session' });
});

it('does not resolve a partial directory or guess between different owners', () => {
  const route = { botId: '20261106_fhrmyly2' };
  const views = [view('managed'), view('team', '447147')];
  expect(resolveConversationRouteTarget(route, [views[0]], false)).toBeUndefined();
  expect(resolveConversationRouteTarget(route, views, true)).toBeUndefined();
  expect(resolveConversationRouteTarget({ ...route, section: 'team' }, views, true)).toBe(views[1]);
  expect(resolveConversationRouteTarget({ botId: views[0].bot.botId }, views, true)).toBe(views[0]);
});

it('matches product IDs even when the canonical BCN identity has a different prefix', () => {
  const target = view('managed');
  target.bot.botId = 'stable-bcn-address:owner';
  expect(resolveConversationRouteTarget({ botId: target.bot.realBotId }, [target], true)).toBe(target);
});
