import type { ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';

function isKnownSessionBot(botId: string, participants: ParticipantView[]): boolean {
  const knownBotIds = participants
    .filter(
      (participant) => participant.kind === 'bot' && typeof participant.actorId === 'string' && participant.actorId,
    )
    .map((participant) => participant.actorId);

  // Session list responses do not contain participants. While the selected
  // Session detail is still hydrating, the scoped BCS WS event is the only
  // authoritative source available to the UI. Once participants are loaded,
  // retain the stricter membership check so stale/non-participant messages do
  // not expose an abort action.
  return knownBotIds.length === 0 || knownBotIds.includes(botId);
}

export function resolveAbortableBotId(message: ChatMessage, participants: ParticipantView[]): string | null {
  if (message.role !== 'assistant' || message.status !== 'streaming') return null;
  const botId = message.extra?.botUuid;
  if (typeof botId !== 'string' || !botId) return null;
  return isKnownSessionBot(botId, participants) ? botId : null;
}

export function isAbortedBotMessage(message: ChatMessage, participants: ParticipantView[]): boolean {
  if (message.role !== 'assistant' || message.status !== 'aborted') return false;
  const botId = message.extra?.botUuid;
  return typeof botId === 'string' && Boolean(botId) && isKnownSessionBot(botId, participants);
}

export function getElapsedSeconds(createdAt: number | undefined, now: number): number | null {
  if (typeof createdAt !== 'number' || !Number.isFinite(createdAt)) return null;
  const timestamp = createdAt < 1_000_000_000_000 ? createdAt * 1000 : createdAt;
  return Math.max(0, Math.floor((now - timestamp) / 1000));
}
