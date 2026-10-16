import type { ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { isAbortedBotMessage } from './groupChatAbortHelpers';

interface GroupChatRunStatusProps {
  message: ChatMessage;
  participants: ParticipantView[];
}

export function GroupChatRunStatus({ message, participants }: GroupChatRunStatusProps) {
  if (isAbortedBotMessage(message, participants)) {
    return <div className="relative z-10 mb-3 ml-10 text-xs text-muted-foreground">已停止</div>;
  }

  return null;
}
