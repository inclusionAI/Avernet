import type { ChatMessage } from '@tc-chat/core';
import type { ParticipantSummaryView } from '@/domain/collaboration';
import { normalizeAvatarUrl } from '@/utils/avatarSource';

export interface BotSenderPresentation { botId: string; name: string; avatarUrl?: string }
function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined;
}
export function resolveBotSenderPresentation(
  message: Pick<ChatMessage, 'extra' | 'status'>,
  session: readonly ParticipantSummaryView[],
  group: readonly ParticipantSummaryView[],
): BotSenderPresentation | undefined {
  const senderId = text(message.extra?.senderId);
  const botUuid = text(message.extra?.botUuid);
  const senderName = text(message.extra?.senderName);
  const botName = text(message.extra?.botName);
  if (message.status === 'pending' && !senderId && !botUuid && !senderName && !botName) return undefined;
  const id = botUuid ?? senderId;
  const match = (rows: readonly ParticipantSummaryView[]) => id
    ? rows.find((p) => p.kind === 'bot' && p.actorId === id) : undefined;
  const participant = match(session) ?? match(group);
  const readable = (name: string | undefined) => name !== senderId && name !== botUuid ? name : undefined;
  return {
    botId: id ?? 'bot',
    name: readable(senderName) ?? readable(botName) ?? participant?.name ?? botName ?? id ?? 'Bot',
    avatarUrl: participant
      ? participant.avatarUrl : normalizeAvatarUrl(message.extra?.senderAvatarUrl),
  };
}
