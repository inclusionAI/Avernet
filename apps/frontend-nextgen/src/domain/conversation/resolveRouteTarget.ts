import type { ConversationBotView, ConversationRouteState } from './types';

/** Full addresses win. Legacy product IDs resolve only after the complete directory arrives. */
export function resolveConversationRouteTarget(
  route: ConversationRouteState,
  views: ConversationBotView[],
  hydrated: boolean,
): ConversationBotView | undefined {
  if (!route.botId) return undefined;
  const candidates = route.section ? views.filter((view) => view.section === route.section) : views;
  const exact = candidates.find((view) => view.bot.botId === route.botId);
  if (exact) return exact;
  if (!hydrated) return undefined;
  const aliases = candidates.filter((view) => view.bot.realBotId === route.botId);
  // Never guess an owner when a product ID names multiple directory entries.
  return aliases.length === 1 ? aliases[0] : undefined;
}
