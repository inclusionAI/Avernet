import type { ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { getElapsedSeconds, isAbortedBotMessage, resolveAbortableBotId } from './groupChatAbortHelpers';

interface GroupChatRunStatusProps {
  message: ChatMessage;
  participants: ParticipantView[];
  now: number;
}

export function GroupChatRunStatus({ message, participants, now }: GroupChatRunStatusProps) {
  if (isAbortedBotMessage(message, participants)) {
    return <div className="relative z-10 mb-3 ml-10 text-xs text-muted-foreground">已终止</div>;
  }

  const botId = resolveAbortableBotId(message, participants);
  if (!botId) return null;
  const elapsed = getElapsedSeconds(message.createdAt, now);

  return (
    <div
      className="relative z-10 mb-3 ml-10 flex items-center gap-1.5 text-xs text-muted-foreground"
      aria-live="polite"
    >
      <span>输出中</span>
      {elapsed === null ? null : (
        <>
          <span aria-hidden="true">·</span>
          <span>总耗时 {elapsed}s</span>
        </>
      )}
    </div>
  );
}
