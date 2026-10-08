import type { BotFriendSessionView } from '@/services/workspace/botFriendConversationService';
import { formatSessionTime, formatSessionTimeTooltip, SessionCard } from '../SessionCard';

interface BotFriendSessionRowProps {
  session: BotFriendSessionView;
  selected: boolean;
  onSelect: (sessionId: string) => void;
}

export function BotFriendSessionRow({ session, selected, onSelect }: BotFriendSessionRowProps) {
  return (
    <SessionCard
      title={session.title}
      subtitle=""
      compact
      dateText={formatSessionTime(session.gmtModified || session.gmtCreate)}
      dateTooltip={formatSessionTimeTooltip(session.gmtModified || session.gmtCreate)}
      selected={selected}
      indicator="message"
      onSelect={() => onSelect(session.sessionId)}
    />
  );
}
