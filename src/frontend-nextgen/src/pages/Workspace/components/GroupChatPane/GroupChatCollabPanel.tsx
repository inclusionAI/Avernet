import type { SessionView } from '@/domain/collaboration';
import type { CollabPanelState } from '@/pages/Workspace/hooks/useCollabPanel';
import type { ChatMessage } from '@tc-chat/core';
import { CollabPanel } from './CollabPanel';
import { GroupChatActiveRuns } from './GroupChatActiveRuns';

export function GroupChatCollabPanel({
  panel,
  session,
  messages,
  abortingBotIds,
  abortBot,
}: {
  panel: CollabPanelState;
  session: SessionView | null;
  messages: ChatMessage[];
  abortingBotIds: ReadonlySet<string>;
  abortBot: (botId: string) => Promise<void>;
}) {
  return (
    <CollabPanel
      panel={panel}
      activeRuns={
        session ? (
          <GroupChatActiveRuns
            messages={messages}
            participants={session.participants}
            abortingBotIds={abortingBotIds}
            onAbortBot={abortBot}
          />
        ) : undefined
      }
    />
  );
}
