import type { SessionView } from '@/domain/collaboration';
import { useEffect, useRef } from 'react';

function isHumanOnlyMention(mentionIds: string[] | undefined, participants: SessionView['participants']): boolean {
  if (!mentionIds?.length) return false;
  const humanIds = new Set(
    participants.filter((participant) => participant.kind === 'human').map((participant) => participant.actorId),
  );
  return mentionIds.every((mentionId) => humanIds.has(mentionId));
}

export function useHumanOnlyChatRequests(chat: { isRequesting: boolean }, participants: SessionView['participants']) {
  const hasBlockingRequestRef = useRef(false);

  useEffect(() => {
    if (chat.isRequesting) return;
    hasBlockingRequestRef.current = false;
  }, [chat.isRequesting]);

  const isSendBlocked = () => chat.isRequesting && hasBlockingRequestRef.current;

  const markRequest = (mentions?: string[]) => {
    if (!isHumanOnlyMention(mentions, participants)) hasBlockingRequestRef.current = true;
  };

  return { isSendBlocked, markRequest };
}
