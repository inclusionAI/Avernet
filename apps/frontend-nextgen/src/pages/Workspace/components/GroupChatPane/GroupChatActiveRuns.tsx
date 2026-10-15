import { Button, Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui';
import type { DeliveryStatusView, ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { ChevronUp, Square } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { buildBotActivityItems } from './congestionHelpers';
import { resolveAbortableBotId } from './groupChatAbortHelpers';
import { GroupChatActivityPanel } from './GroupChatActivityPanel';
import { isBotReplying, type GroupChatActivityActions } from './GroupChatActivityParts';

interface ActiveBotRunSummary {
  botId: string;
  botName: string;
  runCount: number;
  startedAt?: number;
}

export function collectActiveBotRuns(messages: ChatMessage[], participants: ParticipantView[]): ActiveBotRunSummary[] {
  const participantNames = new Map(
    participants
      .filter((participant) => participant.kind === 'bot')
      .map((participant) => [participant.actorId, participant.name]),
  );
  const activeBots = new Map<string, ActiveBotRunSummary>();
  for (const message of messages) {
    const botId = resolveAbortableBotId(message, participants);
    if (!botId) continue;
    const current = activeBots.get(botId);
    if (!current) {
      activeBots.set(botId, {
        botId,
        botName: participantNames.get(botId) || 'Bot',
        runCount: 1,
        startedAt: message.createdAt,
      });
      continue;
    }
    current.runCount += 1;
    if (
      typeof message.createdAt === 'number' &&
      (typeof current.startedAt !== 'number' || message.createdAt < current.startedAt)
    )
      current.startedAt = message.createdAt;
  }
  return [...activeBots.values()];
}

interface GroupChatActiveRunsProps extends GroupChatActivityActions {
  messages: ChatMessage[];
  participants: ParticipantView[];
  queuedDeliveries?: DeliveryStatusView[];
  processingDeliveries?: DeliveryStatusView[];
}

/** 输入框上方固定的轻量活动栏；停止与取消均复用现有控制回调。 */
export function GroupChatActiveRuns({
  messages,
  participants,
  queuedDeliveries = [],
  processingDeliveries = [],
  ...actions
}: GroupChatActiveRunsProps) {
  const [open, setOpen] = useState(false);
  const [selectedBotId, setSelectedBotId] = useState<string | null>(null);
  const activeBots = useMemo(() => collectActiveBotRuns(messages, participants), [messages, participants]);
  const items = useMemo(
    () => buildBotActivityItems({ activeRuns: activeBots, queuedDeliveries, processingDeliveries, participants }),
    [activeBots, queuedDeliveries, processingDeliveries, participants],
  );
  useEffect(() => {
    if (items.length === 0) setOpen(false);
    if (selectedBotId !== null && !items.some((bot) => bot.botId === selectedBotId)) {
      setSelectedBotId(items[0]?.botId ?? null);
    }
  }, [items, selectedBotId]);
  const selectedBot = items.find((bot) => bot.botId === selectedBotId) ?? items[0];
  if (!selectedBot) return null;
  const replyingCount = items.filter(isBotReplying).length;
  const queuedCount = items.reduce((count, bot) => count + bot.queued.length, 0);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="flex w-full min-w-0 flex-wrap items-center justify-between gap-2" aria-label="群聊处理状态">
          <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground" role="status">
            <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
            <span>
              {replyingCount > 0
                ? `${replyingCount} 个 Bot 处理中 · ${queuedCount} 项待处理`
                : `${queuedCount} 项待处理`}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-muted-foreground"
                rightIcon={<ChevronUp className="size-3" aria-hidden />}
              >
                查看处理列表
              </Button>
            </PopoverTrigger>
            {replyingCount > 0 ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-muted-foreground"
                leftIcon={<Square className="size-3" aria-hidden />}
                aria-label="选择要停止回复的 Bot"
                onClick={() => {
                  if (!isBotReplying(selectedBot))
                    setSelectedBotId(items.find(isBotReplying)?.botId ?? selectedBot.botId);
                  setOpen(true);
                }}
              >
                停止…
              </Button>
            ) : null}
          </div>
        </div>
      </PopoverAnchor>
      <PopoverContent
        side="top"
        align="start"
        className="w-[var(--radix-popper-anchor-width)] max-w-[calc(100vw-2rem)] rounded-xl p-0"
      >
        <GroupChatActivityPanel
          items={items}
          selectedBot={selectedBot}
          onSelectBot={setSelectedBotId}
          onClose={() => setOpen(false)}
          {...actions}
        />
      </PopoverContent>
    </Popover>
  );
}
