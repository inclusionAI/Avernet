import {
  Badge,
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import type { DeliveryStatusView, ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { ChevronDown, Square, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { buildBotActivityItems, humanizeWaitReason, type BotActivityItem } from './congestionHelpers';
import { getElapsedSeconds, resolveAbortableBotId } from './groupChatAbortHelpers';

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
    ) {
      current.startedAt = message.createdAt;
    }
  }

  return [...activeBots.values()];
}

interface GroupChatActiveRunsProps {
  messages: ChatMessage[];
  participants: ParticipantView[];
  abortingBotIds: ReadonlySet<string>;
  onAbortBot: (botId: string) => Promise<void>;
  /** 排队中的投递列表（拥塞控制）。 */
  queuedDeliveries?: DeliveryStatusView[];
  /** 处理中的投递列表（拥塞控制）。 */
  processingDeliveries?: DeliveryStatusView[];
  /** 正在取消的投递 ID 集合。 */
  cancellingDeliveryIds?: ReadonlySet<string>;
  /** 取消单个投递。 */
  onCancelDelivery?: (messageId: string, deliveryId: string) => Promise<void>;
}

interface BotActivityChipProps {
  bot: BotActivityItem;
  now: number;
  aborting: boolean;
  onAbortBot: (botId: string) => Promise<void>;
  cancellingDeliveryIds?: ReadonlySet<string>;
  onCancelDelivery?: (messageId: string, deliveryId: string) => Promise<void>;
}

/** 单个 Bot 的活动芯片：输出状态 + 终止按钮 + 排队徽标；有投递内容时可展开该 bot 专属 Popover。 */
function BotActivityChip({
  bot,
  now,
  aborting,
  onAbortBot,
  cancellingDeliveryIds,
  onCancelDelivery,
}: BotActivityChipProps) {
  const [open, setOpen] = useState(false);
  const elapsed = bot.active ? getElapsedSeconds(bot.active.startedAt, now) : null;
  const hasQueueContent = bot.queued.length > 0 || bot.processing.length > 0;

  const info = (
    <div className="min-w-0">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="max-w-28 truncate text-sm font-medium leading-tight text-foreground">{bot.botName}</div>
          </TooltipTrigger>
          <TooltipContent>{bot.botName}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <div className="mt-0.5 flex items-center gap-1 whitespace-nowrap text-xs leading-tight text-muted-foreground tabular-nums">
        {bot.active ? (
          <>
            <span>{bot.active.runCount > 1 ? `${bot.active.runCount} 个输出中` : '输出中'}</span>
            {elapsed === null ? null : (
              <>
                <span aria-hidden="true">·</span>
                <span>{elapsed}s</span>
              </>
            )}
          </>
        ) : null}
        {bot.queued.length > 0 ? (
          <Badge tone="warning" className="text-[10px]">
            {bot.queued.length} 条排队
          </Badge>
        ) : null}
        {hasQueueContent ? <ChevronDown className="h-3 w-3 text-muted-foreground" aria-hidden /> : null}
      </div>
    </div>
  );

  return (
    <div
      data-testid={`active-bot-run-${bot.botId}`}
      className="flex shrink-0 items-center gap-3 rounded-lg border border-border bg-background px-2.5 py-1.5 shadow-sm"
    >
      {hasQueueContent ? (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <div
              data-testid={`bot-queue-trigger-${bot.botId}`}
              className="min-w-0 cursor-pointer"
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setOpen(!open);
                }
              }}
            >
              {info}
            </div>
          </PopoverTrigger>
          <PopoverContent className="w-80 p-0" align="start">
            <div className="max-h-80 overflow-y-auto px-3 py-2">
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <span className="text-sm text-foreground">{bot.botName}</span>
                {bot.processing.length > 0 ? (
                  <Badge tone="primary" className="text-[10px]">
                    {bot.processing.length} 条处理中
                  </Badge>
                ) : null}
                {bot.queued.length > 0 ? (
                  <Badge tone="warning" className="text-[10px]">
                    {bot.queued.length} 条排队
                  </Badge>
                ) : null}
              </div>
              {bot.queued.map((delivery) => {
                const cancelling = cancellingDeliveryIds?.has(delivery.delivery_id) ?? false;
                const contentPreview = delivery.content_preview ?? null;
                return (
                  <div
                    key={delivery.delivery_id}
                    className="mb-1.5 flex items-center justify-between rounded-md bg-muted/50 px-2 py-1.5 last:mb-0"
                  >
                    <div className="min-w-0 flex-1">
                      {contentPreview && <div className="truncate text-xs text-foreground">{contentPreview}</div>}
                      <div className="text-xs text-muted-foreground">{humanizeWaitReason(delivery.wait_reason)}</div>
                    </div>
                    {onCancelDelivery && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="ml-2 h-6 shrink-0 px-1.5 text-xs text-muted-foreground hover:text-destructive"
                        loading={cancelling}
                        disabled={cancelling}
                        aria-label={`取消发往 ${bot.botName} 的排队消息`}
                        leftIcon={<X className="h-3 w-3" />}
                        onClick={() => {
                          void onCancelDelivery(delivery.message_id, delivery.delivery_id);
                        }}
                      >
                        {cancelling ? '取消中…' : '取消'}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          </PopoverContent>
        </Popover>
      ) : (
        info
      )}
      {bot.active ? (
        <Button
          size="sm"
          variant="ghost"
          className="h-7 shrink-0 rounded-md border border-transparent bg-transparent px-2 text-destructive shadow-none hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive focus-visible:border-destructive/30 focus-visible:bg-destructive/10"
          loading={aborting}
          disabled={aborting}
          aria-label={aborting ? `正在终止 ${bot.botName} 的输出` : `终止 ${bot.botName} 的输出`}
          leftIcon={<Square className="h-3 w-3 fill-current" />}
          onClick={() => {
            void onAbortBot(bot.botId);
          }}
        >
          {aborting ? '终止中…' : bot.active.runCount > 1 ? `终止(${bot.active.runCount})` : '终止'}
        </Button>
      ) : null}
    </div>
  );
}

/** "用户发言模式"行内的活动 Bot 模块——按 Bot 合并输出状态、终止操作与排队消息。 */
export function GroupChatActiveRuns({
  messages,
  participants,
  abortingBotIds,
  onAbortBot,
  queuedDeliveries = [],
  processingDeliveries = [],
  cancellingDeliveryIds,
  onCancelDelivery,
}: GroupChatActiveRunsProps) {
  const activeBots = useMemo(() => collectActiveBotRuns(messages, participants), [messages, participants]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (activeBots.length === 0) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeBots.length]);

  const items = useMemo(
    () => buildBotActivityItems({ activeRuns: activeBots, queuedDeliveries, processingDeliveries, participants }),
    [activeBots, queuedDeliveries, processingDeliveries, participants],
  );

  if (items.length === 0) return null;

  return (
    <div className="ml-3 flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="正在输出的 Bot">
      {items.map((bot) => (
        <BotActivityChip
          key={bot.botId}
          bot={bot}
          now={now}
          aborting={abortingBotIds.has(bot.botId)}
          onAbortBot={onAbortBot}
          cancellingDeliveryIds={cancellingDeliveryIds}
          onCancelDelivery={onCancelDelivery}
        />
      ))}
    </div>
  );
}
