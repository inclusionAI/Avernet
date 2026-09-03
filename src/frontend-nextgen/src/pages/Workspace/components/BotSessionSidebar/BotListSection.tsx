import { Button, Skeleton } from '@/components/ui';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { BotSessionPageMeta } from '../../hooks/useBotSessionMap';
import { BotItem } from './BotItem';

interface BotListSectionProps {
  title: string;
  sectionKey: string;
  count: number;
  bots: ChatBotView[];
  isLoading?: boolean;
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
  onManageBot?: (bot: ChatBotView) => void;
}

export function BotListSection({
  title,
  sectionKey,
  count,
  bots,
  isLoading,
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
  onManageBot,
}: BotListSectionProps) {
  const [collapsed, setCollapsed] = useState(!!defaultCollapsed);

  return (
    <div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        aria-label={`${title} (${count})`}
        aria-controls={`bot-section-${sectionKey}`}
        className="flex h-auto min-h-10 w-full items-center gap-1 rounded-none border-b border-border/70 bg-muted/10 px-[18px] py-2 text-xs font-medium text-foreground hover:bg-accent/50"
      >
        {collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="flex-1 text-left">{title}</span>
        <span className="text-muted-foreground">{count} 个 Bot</span>
      </Button>
      {!collapsed && (
        <div id={`bot-section-${sectionKey}`}>
          {isLoading ? (
            <div className="overflow-hidden border-y border-border bg-background">
              {[1, 2].map((i) => (
                <Skeleton.Block
                  key={i}
                  className="h-[72px] w-full rounded-none border-b border-border last:border-b-0"
                />
              ))}
            </div>
          ) : bots.length === 0 ? (
            <div className="flex min-h-[80px] items-center justify-center py-4">
              <span className="text-xs text-muted-foreground">暂无 Bot</span>
            </div>
          ) : (
            <div className="divide-y divide-border/70">
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
                  onManageBot={onManageBot}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
