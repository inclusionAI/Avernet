import type { IdentityView } from '@/domain/collaboration';
import { useMemo } from 'react';
import { useBotFriendHistory } from './useBotFriendHistory';
import { useBotFriendSessions } from './useBotFriendSessions';
import { useBotIdentityFriendDirectory } from './useBotIdentityFriendDirectory';

/** Bot 身份只读好友会话聚合模型：目录 → Session → History。 */
export function useBotFriendConversation(identity: IdentityView | null, enabled: boolean) {
  const botIdentityId = enabled && identity?.kind === 'bot' ? identity.id : null;
  const directory = useBotIdentityFriendDirectory(botIdentityId, Boolean(botIdentityId));
  const sessions = useBotFriendSessions({
    botIdentityId,
    humanFriends: directory.humanFriends,
    directorySettled: directory.settled,
    directoryError: directory.humanError,
  });
  const history = useBotFriendHistory({
    botIdentityId,
    friendUserId: sessions.expandedFriendUserId,
    sessionId: sessions.selectedSession?.sessionId ?? null,
  });
  const selectedFriend = useMemo(
    () => directory.humanFriends.find((friend) => friend.actorId === sessions.expandedFriendUserId) ?? null,
    [directory.humanFriends, sessions.expandedFriendUserId],
  );
  return { ...directory, selectedFriend, sessions, history };
}
