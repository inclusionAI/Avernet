import { Button, Skeleton } from '@/components/ui';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import type { BotSessionPageMeta } from '../../hooks/useBotSessionMap';
import { ListErrorState } from '../ListErrorState';
import { BotItem } from './BotItem';

interface BotListSectionProps {
  title: string;
  sectionKey: string;
  count: number;
  bots: ChatBotView[];
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  defaultCollapsed?: boolean;
  expandedBotIds: Record<string, true>;
  expandedBotSectionKey: Record<string, string>;
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  favoriteSessionsByBotId?: Record<string, BotChatSessionView[]>;
  sessionPageMetaByBotId?: Record<string, BotSessionPageMeta>;
  favoriteSessionPageMetaByBotId?: Record<string, BotSessionPageMeta>;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string, sectionKey?: string) => void;
  onSelectBot: (bot: ChatBotView) => void;
  onSelectSession: (botId: string, sessionId: string) => void;
  onCreateSession: (botId: string) => void;
  onDeleteSession: (botId: string, sessionId: string) => Promise<boolean>;
  onRenameSession: (botId: string, sessionId: string, title: string) => Promise<boolean>;
  onClearSessionContext: (botId: string, sessionId: string) => Promise<boolean>;
  onToggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  onLoadFavorites: (botId: string) => Promise<void>;
  onLoadMoreSessions?: (botId: string, mode: 'all' | 'favorite') => Promise<void>;
  onReloadBot?: (botId: string) => Promise<void>;
  headerHint?: ReactNode;
  footer?: ReactNode;
}

export function BotListSection({
  title,
  sectionKey,
  count,
  bots,
  isLoading,
  error,
  onRetry,
  defaultCollapsed,
  expandedBotIds,
  expandedBotSectionKey,
  sessionsByBotId,
  favoriteSessionsByBotId,
  sessionPageMetaByBotId,
  favoriteSessionPageMetaByBotId,
  selectedBotSessionId,
  onToggleBotExpanded,
  onSelectBot,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  onClearSessionContext,
  onToggleFavorite,
  onLoadFavorites,
  onLoadMoreSessions,
  onReloadBot,
  headerHint,
  footer,
}: BotListSectionProps) {
  const [collapsed, setCollapsed] = useState(!!defaultCollapsed);

  return (
    <div>
      {/* 验收微调：分组头去掉常驻灰底与下边框（靠留白与字重区分分组头）；
          hover 反馈由按钮自身 hover:bg-accent/50 承担。 */}
      <div className="flex min-h-9 items-center">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          aria-label={`${title} (${count})`}
          aria-controls={`bot-section-${sectionKey}`}
          className="flex h-auto min-h-9 min-w-0 flex-1 items-center gap-1 rounded-none border-0 bg-transparent px-4 py-2 text-xs font-medium text-foreground hover:bg-accent/50"
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate text-left">
            {title} ({count})
          </span>
        </Button>
        {headerHint && <div className="shrink-0 pr-3">{headerHint}</div>}
      </div>
      {!collapsed && (
        <div id={`bot-section-${sectionKey}`}>
          {error ? (
            <ListErrorState message={error} onRetry={onRetry} />
          ) : isLoading ? (
            /* 验收微调：加载骨架去边框与行底，与列表通透风格一致。 */
            <div className="overflow-hidden">
              {[1, 2].map((i) => (
                <Skeleton.Block key={i} className="h-16 w-full rounded-none" />
              ))}
            </div>
          ) : bots.length === 0 ? (
            <div className="flex min-h-[80px] items-center justify-center py-4">
              <span className="text-xs text-muted-foreground">暂无 Bot</span>
            </div>
          ) : (
            /* 验收微调：Bot 条目间分割线去除，靠留白与 hover 区分行。 */
            <div>
              {bots.map((bot) => (
                <BotItem
                  key={`${sectionKey}:${bot.botId}`}
                  bot={bot}
                  expanded={!!expandedBotIds[bot.botId] && expandedBotSectionKey[bot.botId] === sectionKey}
                  sessions={sessionsByBotId[bot.botId]}
                  favoriteSessions={favoriteSessionsByBotId?.[bot.botId]}
                  allSessionMeta={sessionPageMetaByBotId?.[bot.botId]}
                  favoriteSessionMeta={favoriteSessionPageMetaByBotId?.[bot.botId]}
                  selectedBotSessionId={selectedBotSessionId}
                  onToggleBotExpanded={(botId) => onToggleBotExpanded(botId, sectionKey)}
                  onSelectBot={onSelectBot}
                  onSelectSession={onSelectSession}
                  onCreateSession={onCreateSession}
                  onDeleteSession={onDeleteSession}
                  onRenameSession={onRenameSession}
                  onClearSessionContext={onClearSessionContext}
                  onToggleFavorite={onToggleFavorite}
                  onLoadFavorites={onLoadFavorites}
                  onLoadMoreSessions={onLoadMoreSessions}
                  onReloadBot={onReloadBot}
                />
              ))}
            </div>
          )}
          {footer}
        </div>
      )}
    </div>
  );
}
