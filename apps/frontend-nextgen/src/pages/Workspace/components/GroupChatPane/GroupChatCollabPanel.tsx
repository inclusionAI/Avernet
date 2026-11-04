import type { DeliveryStatusView, GroupView, SessionView } from '@/domain/collaboration';
import type { CollabPanelState } from '@/pages/Workspace/hooks/useCollabPanel';
import type { ChatMessage } from '@tc-chat/core';
import { getExtensionSlots } from '@/extensions/slots';
import { CollabPanel } from './CollabPanel';
import { buildBotActivityItems } from './congestionHelpers';
import { collectActiveBotRuns, GroupChatActiveRuns } from './GroupChatActiveRuns';

// task_master_slave 任务栏槽位（collab 形态 null：只保留 ActiveRuns + CollabPanel）。
const GroupTaskRail = getExtensionSlots().groupTasks?.ManagerWorkerTasks;

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
    const managerQueued = queuedDeliveries?.filter(isManagerMessage) ?? [];
    const managerProcessing = processingDeliveries?.filter(isManagerMessage) ?? [];
    // 带边框容器的渲染条件（collab-form 精简回归）：显式镜像 GroupChatActiveRuns 内部
    // items 判空口径（collectActiveBotRuns ∪ queued/processing 投递，见 congestionHelpers）。
    // - 任务栏槽位非空（Open）恒渲染容器 → DOM 与既有行为完全恒等；
    // - collab 轨缺席时仅在有活动内容才渲染，否则空 border-t 容器残留一条游离分隔线；
    //   有活动内容（Bot 回复中/待处理投递）时活动条仍在原带边框容器内展示。
    const hasRunStrip =
      !!GroupTaskRail ||
      buildBotActivityItems({
        activeRuns: collectActiveBotRuns(managerMessages, managers),
        queuedDeliveries: managerQueued,
        processingDeliveries: managerProcessing,
        participants: managers,
      }).length > 0;
    return (
      <>
        {hasRunStrip ? (
          <div className="border-t border-border bg-background px-3 pb-1 pt-1.5 sm:px-6">
            <div className="mx-auto w-[calc(100%-64px)] pl-3 pr-1.5">
              <GroupChatActiveRuns
                key={`manager-${session.sessionId}`}
                managerMode
                messages={managerMessages}
                participants={managers}
                abortingBotIds={abortingBotIds}
                onAbortBot={abortBot}
                queuedDeliveries={managerQueued}
                processingDeliveries={managerProcessing}
                cancellingDeliveryIds={cancellingDeliveryIds}
                onCancelDelivery={onCancelDelivery}
              />
              {GroupTaskRail ? (
                <GroupTaskRail key={session.sessionId} session={session} messages={messages} connected={connected} />
              ) : null}
            </div>
          </div>
        ) : null}
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
