import { Badge, Button, Card, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotTypeLabel } from '@/domain/botType';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Plus } from 'lucide-react';
import React, { useState } from 'react';
import { AvatarTile } from '../AvatarTile';
import { SessionTabs, type SessionTabValue } from '../SessionTabs';
import { BotSessionItem } from './BotSessionItem';

interface BotItemProps {
  bot: ChatBotView;
  expanded: boolean;
  /** 会话列表；undefined 表示加载中（首次拉取尚未完成）。 */
  sessions: BotChatSessionView[] | undefined;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string) => void;
  onSelectSession: (botId: string, sessionId: string) => void;
  onCreateSession: (botId: string) => void;
  onDeleteSession: (botId: string, sessionId: string) => Promise<boolean>;
  onRenameSession: (botId: string, sessionId: string, title: string) => Promise<boolean>;
  onClearSessionContext: (botId: string, sessionId: string) => Promise<boolean>;
  onToggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  onLoadFavorites: (botId: string) => Promise<void>;
}

/**
 * Bot 条目:头像 + 名称 + 引擎徽章,右侧 + 新建会话;点击条目展开/收起,展开后列出会话。
 * 不可聊置灰且不响应点击。展开为手风琴互斥(同一时间仅一个 bot 展开),
 * 高亮仅跟随 expanded,避免「选中会话所在 bot」在切换到其它 bot 后仍保持蓝底。
 */
export const BotItem = React.memo(function BotItem({
  bot,
  expanded,
  sessions,
  selectedBotSessionId,
  onToggleBotExpanded,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  onClearSessionContext,
  onToggleFavorite,
  onLoadFavorites,
}: BotItemProps) {
  const toggle = () => {
    if (bot.chatable) onToggleBotExpanded(bot.botId);
  };
  const handleCreateSession = (e: React.MouseEvent) => {
    e.stopPropagation();
    onCreateSession(bot.botId);
  };
  const [sessionTab, setSessionTab] = useState<SessionTabValue>('all');

  return (
    <div className="space-y-2">
      <Card
        className={cn(
          'flex items-center gap-1 rounded-lg p-1 transition-colors',
          expanded && 'border-primary bg-primary/5',
          !bot.chatable && 'opacity-50',
        )}
      >
        <Button
          variant="ghost"
          aria-label={bot.chatable ? bot.displayName : '不可用 Bot'}
          aria-expanded={expanded}
          aria-disabled={!bot.chatable}
          onClick={toggle}
          className={cn(
            'flex h-auto min-w-0 flex-1 items-center justify-start gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-transparent',
            !bot.chatable && 'cursor-not-allowed',
          )}
        >
          <AvatarTile src={bot.avatarUrl} label={bot.displayName} />
          <div className="min-w-0 flex-1">
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="block truncate text-sm font-semibold text-foreground">{bot.displayName}</span>
                </TooltipTrigger>
                <TooltipContent>{bot.chatable ? bot.displayName : '该 Bot 暂不支持单聊'}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <div className="mt-0.5 flex items-center gap-1.5">
              <Badge tone={bot.chatable ? 'primary' : 'neutral'}>
                {bot.chatable ? bot.engine || 'OpenClaw' : '暂不支持'}
              </Badge>
              {bot.chatable && getBotTypeLabel(bot.botType) && (
                <Badge tone="neutral">{getBotTypeLabel(bot.botType)}</Badge>
              )}
            </div>
          </div>
        </Button>
        {bot.chatable && (
          <IconButton label="新建会话" size="sm" icon={<Plus className="h-4 w-4" />} onClick={handleCreateSession} />
        )}
      </Card>

      {expanded && bot.chatable && (
        <div className="pl-3">
          <SessionTabs
            allCount={sessions?.length ?? 0}
            favoriteCount={sessions?.filter((s) => s.favorite).length ?? 0}
            showCount
            value={sessionTab}
            onChange={(t) => {
              setSessionTab(t);
              if (t === 'favorite') void onLoadFavorites(bot.botId);
            }}
          />
          <div className="mt-2 overflow-hidden rounded-lg border border-border bg-card">
            {sessions === undefined ? (
              <div>
                {[1, 2, 3].map((i) => (
                  <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
                ))}
              </div>
            ) : sessions.filter((s) => sessionTab === 'all' || s.favorite).length === 0 ? (
              <div className="px-3 py-4">
                <span className="text-xs text-muted-foreground">暂无会话</span>
              </div>
            ) : (
              sessions
                .filter((s) => sessionTab === 'all' || s.favorite)
                .map((s) => (
                  <BotSessionItem
                    key={s.sessionId}
                    session={s}
                    selected={selectedBotSessionId === s.sessionId}
                    onSelect={(sid) => onSelectSession(bot.botId, sid)}
                    onDelete={(sid) => onDeleteSession(bot.botId, sid)}
                    onRename={(sid, title) => onRenameSession(bot.botId, sid, title)}
                    onClearContext={(sid) => onClearSessionContext(bot.botId, sid)}
                    favorite={s.favorite}
                    onToggleFavorite={(sid) => onToggleFavorite(bot.botId, sid)}
                  />
                ))
            )}
          </div>
        </div>
      )}
    </div>
  );
});
