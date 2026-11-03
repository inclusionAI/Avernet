import type { DeliveryStatusView, GroupView, SessionView } from '@/domain/collaboration';
import type { CollabPanelState } from '@/pages/Workspace/hooks/useCollabPanel';
import type { ChatMessage } from '@tc-chat/core';
import { ManagerWorkerTasks } from './ManagerWorkerTasks';
import { CollabPanel } from './CollabPanel';
import { GroupChatActiveRuns } from './GroupChatActiveRuns';

export function GroupChatCollabPanel({
  panel,
  session,
  group,
  messages,
  abortingBotIds,
  abortBot,
  queuedDeliveries,
  processingDeliveries,
  cancellingDeliveryIds,
  onCancelDelivery,
  connected = true,
}: {
  connected?: boolean;
  panel: CollabPanelState;
  session: SessionView | null;
  group: GroupView | null;
  messages: ChatMessage[];
  abortingBotIds: ReadonlySet<string>;
  abortBot: (botId: string) => Promise<void>;
  queuedDeliveries?: DeliveryStatusView[];
  processingDeliveries?: DeliveryStatusView[];
  cancellingDeliveryIds?: ReadonlySet<string>;
  onCancelDelivery?: (messageId: string, deliveryId: string) => Promise<void>;
}) {
  if (session && group?.kind === 'task_master_slave') {
    const managers = session.participants.filter((participant) => participant.kind === 'bot' && participant.role === 'manager');
    const isManagerMessage = (delivery: DeliveryStatusView) =>
      delivery.flow_kind === 'group' && managers.some((manager) => manager.actorId === delivery.target_bot_id);
    const managerMessages = messages.filter((message) => managers.some((manager) => manager.actorId === message.extra?.botUuid));
    return (
      <>
        <div className="border-t border-border bg-background px-3 pb-1 pt-1.5 sm:px-6">
          <div className="mx-auto w-[calc(100%-64px)] pl-3 pr-1.5">
            <GroupChatActiveRuns
              key={`manager-${session.sessionId}`}
              managerMode
              messages={managerMessages}
              participants={managers}
              abortingBotIds={abortingBotIds}
              onAbortBot={abortBot}
              queuedDeliveries={queuedDeliveries?.filter(isManagerMessage)}
              processingDeliveries={processingDeliveries?.filter(isManagerMessage)}
              cancellingDeliveryIds={cancellingDeliveryIds}
              onCancelDelivery={onCancelDelivery}
            />
            <ManagerWorkerTasks key={session.sessionId} session={session} messages={messages} connected={connected} />
          </div>
        </div>
        <CollabPanel panel={panel} />
      </>
    );
  }
  return (
    <CollabPanel
      panel={panel}
      activeRuns={
        session ? (
          <GroupChatActiveRuns
            key={session.sessionId}
            messages={messages}
            participants={session.participants}
            abortingBotIds={abortingBotIds}
            onAbortBot={abortBot}
            queuedDeliveries={queuedDeliveries}
            processingDeliveries={processingDeliveries}
            cancellingDeliveryIds={cancellingDeliveryIds}
            onCancelDelivery={onCancelDelivery}
          />
        ) : undefined
      }
    />
  );
}
