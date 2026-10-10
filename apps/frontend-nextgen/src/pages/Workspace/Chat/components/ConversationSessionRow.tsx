// 会话行:只读(origin=others 的他人会话)与交互式(我发起 / 好友 Bot)共用一行结构。
// 只读形态不渲染任何写操作入口(收藏 / 更多 / 新建),仅打开只读历史;
// 交互式行通过 Hook 回调(选中 / 加载更多)委托既有会话操作,不在行内写业务规则。
import { Badge, Button, IconButton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { MessageSquare, Star } from 'lucide-react';
import React from 'react';

export interface ConversationSessionRowProps {
  session: BotChatSessionView;
  selected: boolean;
  /** 只读会话(他人发起 / 好友视角):仅打开历史,无任何写操作。 */
  readOnly?: boolean;
  onSelect(): void;
  onToggleFavorite?(): void;
  favoritePending?: boolean;
}

export const ConversationSessionRow = React.memo(function ConversationSessionRow({
  session,
  selected,
  readOnly = false,
  onSelect,
  onToggleFavorite,
  favoritePending = false,
}: ConversationSessionRowProps) {
  return (
    <div
      className={cn(
        'group/row relative flex items-center px-4 py-0.5 transition-colors',
        'last:[&_[data-session-tree-rail]]:bottom-1/2',
        selected ? 'bg-muted' : 'hover:bg-accent/50',
      )}
    >
      {/* 与协作群一致：逐行连接，末行竖线止于横向分支。 */}
      <span data-session-tree-rail aria-hidden="true" className="absolute -left-2 top-0 bottom-0 w-px bg-border" />
      <span data-session-tree-elbow aria-hidden="true" className="absolute -left-2 top-1/2 h-px w-2 bg-border" />
      {selected && (
        <span aria-hidden="true" className="absolute bottom-1.5 left-0 top-1.5 w-[3px] rounded-r-sm bg-primary" />
      )}
      <Button
        variant="ghost"
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className="flex h-auto min-w-0 flex-1 items-center justify-start gap-2 rounded-none px-0 py-2 text-left hover:bg-transparent"
      >
        <MessageSquare
          className={cn('h-3.5 w-3.5 shrink-0', selected ? 'text-primary' : 'text-muted-foreground')}
          aria-hidden="true"
        />
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-xs leading-5',
            selected ? 'font-medium text-primary' : 'font-normal text-foreground',
          )}
        >
          {session.title}
        </span>
        {readOnly ? (
          <Badge tone="neutral" className="shrink-0 rounded px-1.5 py-0 text-[10px] leading-4">
            只读
          </Badge>
        ) : !onToggleFavorite ? (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {session.messageCount > 0 ? `${session.messageCount} 条` : null}
          </span>
        ) : null}
      </Button>
      {!readOnly && onToggleFavorite && (
        <IconButton
          label={
            session.favorite === undefined
              ? '收藏状态暂不可用，请重新加载会话列表'
              : session.favorite
              ? '取消收藏'
              : '收藏会话'
          }
          ariaLabel={session.favorite ? '取消收藏' : '收藏会话'}
          size="sm"
          icon={
            <Star className={cn('h-4 w-4', session.favorite ? 'fill-warning text-warning' : 'text-muted-foreground')} />
          }
          disabled={favoritePending || session.favorite === undefined}
          aria-busy={favoritePending}
          className={cn(
            'ml-1',
            !session.favorite &&
              !selected &&
              'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
          onClick={(event) => {
            event.stopPropagation();
            onToggleFavorite();
          }}
        />
      )}
      {!readOnly && onToggleFavorite && (
        // 固定条数列，零条也占位，避免数字位数变化导致左侧收藏星标错列。
        <span className="ml-2 flex w-14 shrink-0 justify-end">
          {session.messageCount > 0 && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    tabIndex={-1}
                    className="h-auto w-full min-w-0 justify-end rounded-none px-0 py-2 text-xs tabular-nums text-muted-foreground hover:bg-transparent"
                    onClick={onSelect}
                  >
                    <span className="truncate">{session.messageCount} 条</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{session.messageCount} 条消息</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </span>
      )}
    </div>
  );
});
