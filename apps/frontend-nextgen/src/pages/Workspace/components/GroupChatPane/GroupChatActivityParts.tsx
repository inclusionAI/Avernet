import { Avatar, Button, Empty } from '@/components/ui';
import type { DeliveryStatusView } from '@/domain/collaboration';
import { cn } from '@/utils/cn';
import { Clock3, Square } from 'lucide-react';
import type { BotActivityItem } from './congestionHelpers';
import { ReplyProgressIndicator } from './ReplyProgressIndicator';

export interface GroupChatActivityActions {
  abortingBotIds: ReadonlySet<string>;
  onAbortBot: (botId: string) => Promise<void>;
  cancellingDeliveryIds?: ReadonlySet<string>;
  onCancelDelivery?: (messageId: string, deliveryId: string) => Promise<void>;
}

export function isBotReplying(bot: BotActivityItem): boolean {
  return bot.active !== null || bot.processing.length > 0;
}

export function BotActivityIdentity({ bot, compact = false }: { bot: BotActivityItem; compact?: boolean }) {
  return (
    <>
      {isBotReplying(bot) ? (
        <ReplyProgressIndicator />
      ) : (
        <Clock3 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      )}
      <Avatar name={bot.botName} size={28} />
      <span className={cn('flex min-w-0 gap-2', compact ? 'flex-1 flex-col items-start gap-0.5' : 'items-center')}>
        <span className="max-w-full truncate text-sm font-medium text-foreground">{bot.botName}</span>
        {bot.queued.length > 0 ? (
          <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{bot.queued.length} 条排队</span>
        ) : null}
      </span>
    </>
  );
}

export function StopBotReply({
  bot,
  abortingBotIds,
  onAbortBot,
}: { bot: BotActivityItem } & Pick<GroupChatActivityActions, 'abortingBotIds' | 'onAbortBot'>) {
  if (!isBotReplying(bot)) return null;
  const aborting = abortingBotIds.has(bot.botId);
  return (
    <Button
      size="sm"
      variant="ghost"
      className="h-7 shrink-0 px-2 text-xs text-muted-foreground @max-[24rem]:size-11 @max-[24rem]:p-0"
      loading={aborting}
      disabled={aborting}
      aria-label={aborting ? `正在停止 ${bot.botName} 的当前回复` : `停止 ${bot.botName} 的当前回复`}
      leftIcon={<Square className="size-3" aria-hidden />}
      onClick={() => void onAbortBot(bot.botId)}
    >
      <span className="@max-[24rem]:sr-only">{aborting ? '停止中…' : '停止回复'}</span>
    </Button>
  );
}

export function BotActivityHeader({ bot, ...actions }: { bot: BotActivityItem } & GroupChatActivityActions) {
  return (
    <div data-testid={`active-bot-run-${bot.botId}`} className="flex min-w-0 flex-wrap items-center gap-2 pb-2">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <BotActivityIdentity bot={bot} />
      </div>
      <StopBotReply bot={bot} {...actions} />
    </div>
  );
}

export function BotDeliveryQueue({
  bot,
  cancellingDeliveryIds,
  onCancelDelivery,
}: { bot: BotActivityItem } & Pick<GroupChatActivityActions, 'cancellingDeliveryIds' | 'onCancelDelivery'>) {
  if (bot.queued.length === 0) {
    return (
      <Empty
        compact
        title="没有排队消息"
        icon={<Clock3 className="size-4" aria-hidden />}
        className="py-3 [&>div]:mb-1 [&>div]:size-7 [&>p]:text-xs"
      />
    );
  }
  return (
    <ul aria-label={`${bot.botName} 的排队消息`} className="divide-y divide-divider">
      {bot.queued.map((delivery: DeliveryStatusView) => {
        const cancelling = cancellingDeliveryIds?.has(delivery.delivery_id) ?? false;
        return (
          <li key={delivery.delivery_id} className="flex min-w-0 items-center gap-2 py-2.5">
            <Clock3 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1 break-words text-sm text-foreground">
              {delivery.content_preview?.trim() || '消息内容暂不可用'}
            </span>
            {onCancelDelivery ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 shrink-0 px-2 text-xs text-muted-foreground"
                loading={cancelling}
                disabled={cancelling}
                aria-label={`取消发往 ${bot.botName} 的排队消息：${
                  delivery.content_preview?.trim() || '消息内容暂不可用'
                }`}
                onClick={() => void onCancelDelivery(delivery.message_id, delivery.delivery_id)}
              >
                {cancelling ? '取消中…' : '取消'}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
