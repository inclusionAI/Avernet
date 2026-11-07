import { Button, Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui';
import { ChevronUp } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { BotActivityItem } from './congestionHelpers';
import { BotDeliveryQueue, isBotReplying, StopBotReply, type GroupChatActivityActions } from './GroupChatActivityParts';

export function ManagerActivityBar({ items, ...actions }: { items: BotActivityItem[] } & GroupChatActivityActions) {
  const [open, setOpen] = useState(false);
  const replying = items.filter(isBotReplying);
  const queued = items.filter((bot) => bot.queued.length > 0);
  const queuedCount = queued.reduce((count, bot) => count + bot.queued.length, 0);
  useEffect(() => {
    if (queuedCount === 0) setOpen(false);
  }, [queuedCount]);

  return (
    <Popover open={open && queuedCount > 0} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="flex min-h-8 w-full min-w-0 flex-wrap items-center justify-between gap-2" aria-label="Manager 处理状态">
          <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground" role="status">
            <span className="flex size-3.5 shrink-0 items-center justify-center" aria-hidden>
              <span className="size-1.5 rounded-full bg-primary" />
            </span>
            <span>{replying.length > 0 ? 'Manager 处理中' : 'Manager 有待处理消息'}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {queuedCount > 0 ? (
              <PopoverTrigger asChild>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" rightIcon={<ChevronUp className="size-3" aria-hidden />}>
                  待处理 {queuedCount} 条
                </Button>
              </PopoverTrigger>
            ) : null}
            {replying.map((bot) => (
              <div key={bot.botId} className="flex items-center gap-1">
                {replying.length > 1 ? <span className="text-xs text-muted-foreground">{bot.botName}</span> : null}
                <StopBotReply bot={bot} {...actions} />
              </div>
            ))}
          </div>
        </div>
      </PopoverAnchor>
      {queuedCount > 0 ? (
        <PopoverContent side="top" align="start" className="w-[var(--radix-popper-anchor-width)] max-w-[calc(100vw-2rem)] rounded-xl p-4">
          <section aria-label="Manager 待处理消息" className="max-h-80 overflow-y-auto">
            <h3 className="mb-2 text-sm font-medium">待处理消息</h3>
            {queued.map((bot) => (
              <div key={bot.botId}>
                {queued.length > 1 ? <h4 className="text-xs text-muted-foreground">{bot.botName}</h4> : null}
                <BotDeliveryQueue bot={bot} {...actions} />
              </div>
            ))}
          </section>
        </PopoverContent>
      ) : null}
    </Popover>
  );
}
