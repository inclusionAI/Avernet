import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { ChevronRight, X } from 'lucide-react';
import type { BotActivityItem } from './congestionHelpers';
import {
  BotActivityHeader,
  BotActivityIdentity,
  BotDeliveryQueue,
  StopBotReply,
  type GroupChatActivityActions,
} from './GroupChatActivityParts';

interface GroupChatActivityPanelProps extends GroupChatActivityActions {
  items: BotActivityItem[];
  selectedBot: BotActivityItem;
  onSelectBot: (botId: string) => void;
  onClose: () => void;
}

/** 根据活动 Bot 数切换布局；小容器始终纵向排列。 */
export function GroupChatActivityPanel({
  items,
  selectedBot,
  onSelectBot,
  onClose,
  ...actions
}: GroupChatActivityPanelProps) {
  const isBotList = items.length >= 3;
  return (
    <section
      className="@container"
      aria-label="群聊处理列表"
      data-layout={isBotList ? 'bot-list' : items.length === 2 ? 'columns' : 'single'}
    >
      <div className="flex items-center justify-between border-b border-divider px-4 py-2">
        <h3 className="text-sm font-medium">处理列表</h3>
        <Button variant="ghost" size="icon" className="size-7" aria-label="收起处理列表" onClick={onClose}>
          <X className="size-4" aria-hidden />
        </Button>
      </div>
      <div className="max-h-[min(24rem,calc(var(--radix-popover-content-available-height)-3.5rem))] overflow-y-auto p-4">
        {isBotList ? (
          <div className="grid min-w-0 gap-4 @min-[36rem]:grid-cols-[minmax(0,0.9fr)_minmax(0,1.3fr)]">
            <ul aria-label="活动 Bot" className="min-w-0 divide-y divide-divider">
              {items.map((bot) => (
                <li
                  key={bot.botId}
                  data-testid={`active-bot-run-${bot.botId}`}
                  className="flex min-w-0 items-center gap-1 py-2 first:pt-0"
                >
                  <Button
                    variant="ghost"
                    className={cn(
                      'h-auto min-w-0 flex-1 justify-start gap-2 px-1 py-2',
                      selectedBot.botId === bot.botId && 'bg-primary/5',
                    )}
                    aria-label={`查看 ${bot.botName} 的排队消息`}
                    aria-pressed={selectedBot.botId === bot.botId}
                    onClick={() => onSelectBot(bot.botId)}
                  >
                    <BotActivityIdentity bot={bot} compact />
                    <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground" aria-hidden />
                  </Button>
                  <StopBotReply bot={bot} {...actions} />
                </li>
              ))}
            </ul>
            <div className="min-w-0 border-t border-divider pt-3 @min-[36rem]:border-l @min-[36rem]:border-t-0 @min-[36rem]:pl-4 @min-[36rem]:pt-0">
              <h4 className="mb-2 text-sm font-medium">{selectedBot.botName} · 待处理消息</h4>
              <BotDeliveryQueue bot={selectedBot} {...actions} />
            </div>
          </div>
        ) : (
          <div className={cn('grid min-w-0 gap-4', items.length === 2 && '@min-[36rem]:grid-cols-2')}>
            {items.map((bot) => (
              <div key={bot.botId} className="min-w-0">
                <BotActivityHeader bot={bot} {...actions} />
                <div className="border-t border-divider">
                  <BotDeliveryQueue bot={bot} {...actions} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
