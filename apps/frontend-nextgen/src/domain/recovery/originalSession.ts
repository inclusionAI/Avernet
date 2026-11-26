import { serializeConversationRoute } from '@/domain/conversation/route';

type RecoverySessionReference = {
  botId: string;
  ownerId: string;
  sessionKey: string;
};

export function recoveryOriginalSessionPath({
  botId,
  ownerId,
  sessionKey,
}: RecoverySessionReference): string | undefined {
  if (!botId.trim() || !ownerId.trim() || !sessionKey.trim()) return undefined;

  const creator = /:user:([^:]+)$/.exec(sessionKey)?.[1];
  const otherUser = creator && creator !== ownerId ? creator : undefined;
  const search = serializeConversationRoute({
    section: 'managed',
    botId: `${botId}:${ownerId}`,
    origin: otherUser ? 'others' : 'mine',
    friendUserId: otherUser,
    // The chat route's session identifier is the logical key, including its full namespace.
    sessionId: sessionKey,
  });
  return `/workspace/chat?${search}`;
}
