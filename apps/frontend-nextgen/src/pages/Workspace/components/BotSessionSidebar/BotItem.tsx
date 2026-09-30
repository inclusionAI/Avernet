import { Badge, Button, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel, supportsBotSessionFavorites } from '@/domain/botEngine';
import { getBotTypeLabel } from '@/domain/botType';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import type { BotSessionPageMeta } from '../../hooks/useBotSessionMap';
import { AvatarTile } from '../AvatarTile';
import { SIDEBAR_TAG_CLASS } from '../GroupSidebar/GroupItem.types';
import { ListErrorState } from '../ListErrorState';
import { SessionScopeFilter } from '../SessionScopeFilter';
import { BotSessionItem } from './BotSessionItem';

interface BotItemProps {
  bot: ChatBotView;
  expanded: boolean;
  sessions: BotChatSessionView[] | undefined;
  favoriteSessions?: BotChatSessionView[];
  allSessionMeta?: BotSessionPageMeta;
  favoriteSessionMeta?: BotSessionPageMeta;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string) => void;
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
}

/** Bot 行与协作群行共享同一高度和信息层级；AgentCoding Bot 由工作台引导使用。 */
export const BotItem = React.memo(function BotItem({
  bot,
  expanded,
  sessions,
  favoriteSessions,
  allSessionMeta,
  favoriteSessionMeta,
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
}: BotItemProps) {
  const [sessionTab, setSessionTab] = useState<'all' | 'favorite'>('all');
  const canFavoriteSessions = supportsBotSessionFavorites(bot.engine);
  const favoritePrefetchRef = useRef(false);
  useEffect(() => {
    if (!expanded) {
      favoritePrefetchRef.current = false;
      return;
    }
    if (!canFavoriteSessions || !bot.chatable || favoriteSessionMeta !== undefined || favoritePrefetchRef.current)
      return;
    favoritePrefetchRef.current = true;
    void onLoadFavorites(bot.botId);
  }, [bot.botId, bot.chatable, canFavoriteSessions, expanded, favoriteSessionMeta, onLoadFavorites]);
  const toggle = () => {
    if (!bot.chatable) return;
    if (bot.isAgentCodingBot) {
      onSelectBot(bot);
      return;
    }
    onToggleBotExpanded(bot.botId);
  };
  const isUnavailable = !bot.chatable;
  const botEngineLabel = getBotEngineLabel(bot.engine);
  const botTypeLabel = getBotTypeLabel(bot.botType);
  const isCurrent = [sessions, favoriteSessions]
    .filter(Boolean)
    .some((list) => list?.some((session) => session.sessionId === selectedBotSessionId));
  const handleSessionScopeChange = (tab: 'all' | 'favorite') => {
    setSessionTab(tab);
    if (tab === 'favorite') void onLoadFavorites(bot.botId);
    if (!expanded) onToggleBotExpanded(bot.botId);
  };

  return (
    <div>
      <div
        className={cn(
          'group relative flex min-h-16 items-center gap-3 px-4 py-2.5 transition-colors',
          isCurrent || expanded ? 'bg-muted' : 'hover:bg-accent/50',
        )}
      >
        {(isCurrent || expanded) && (
          <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r-sm bg-primary" />
        )}
        <Button
          variant="ghost"
          aria-label={bot.chatable ? bot.displayName : '不可用 Bot'}
          aria-expanded={expanded}
          aria-current={isCurrent ? 'page' : undefined}
          aria-disabled={isUnavailable}
          onClick={toggle}
          className={cn(
            'flex h-auto min-w-0 flex-1 items-center justify-start gap-3 rounded-none px-0 py-1 text-left hover:bg-transparent',
            isUnavailable && 'cursor-not-allowed opacity-50',
          )}
        >
          {/* v1.4：选中/展开态头像品牌浅底弱化强调（浅蓝底+蓝文字+浅蓝环，实心反色试装后按用户反馈调轻）。 */}
          <AvatarTile
            src={bot.avatarUrl}
            label={bot.displayName}
            className={isCurrent || expanded ? 'bg-primary/15 text-primary ring-1 ring-primary/30' : undefined}
            fallbackContent={<span className="text-[10px] font-semibold tracking-[0.08em]">BOT</span>}
          />
          <div className="min-w-0 flex-1">
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="block truncate text-sm font-medium text-foreground">{bot.displayName}</span>
                </TooltipTrigger>
                <TooltipContent>{bot.chatable ? bot.displayName : '该 Bot 状态异常'}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <div className="mt-1 flex min-w-0 items-center gap-1 truncate text-xs leading-4 text-muted-foreground group-hover:pr-20">
              {(bot.isAgentCodingBot ? bot.templateName || 'AgentCoding' : botEngineLabel) && (
                <Badge tone="primary" className={SIDEBAR_TAG_CLASS}>
                  {bot.isAgentCodingBot ? bot.templateName || 'AgentCoding' : botEngineLabel}
                </Badge>
              )}
              {(bot.isAgentCodingBot ? bot.templateName || 'AgentCoding' : botEngineLabel) && botTypeLabel && (
                <Badge tone="primary" className={SIDEBAR_TAG_CLASS}>
                  {botTypeLabel}
                </Badge>
              )}
              {isUnavailable && (
                <Badge tone="neutral" className={SIDEBAR_TAG_CLASS}>
                  状态异常
                </Badge>
              )}
            </div>
          </div>
        </Button>
        {bot.chatable && !bot.isAgentCodingBot && (
          /* v1.5：操作区绝对定位悬浮不占位（与协作群 GroupItem 同构）——badge/Bot 名用满行宽，
             解决透明占位把不可收缩的 badge 挤出截断。满行遮盖（inset-y-0 撑满行高、右缘止于
             箭头左缘），底色走 global.css 工具类与行同合成方式（零色差、不透字）；显隐语义保留。 */
          <div
            className={cn(
              'absolute inset-y-0 right-[30px] z-10 flex items-center gap-0.5 pl-5',
              'mask-[linear-gradient(to_right,transparent,black_20px)]',
              isCurrent || expanded ? 'sidebar-actions-cover-selected' : 'sidebar-actions-cover-hover',
              'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
              (isCurrent || expanded || sessionTab === 'favorite') && 'opacity-100',
            )}
            onClick={(event) => event.stopPropagation()}
          >
            <IconButton
              label="新建会话"
              size="sm"
              icon={<Plus className="h-3.5 w-3.5" />}
              className="h-6 w-6 rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
              onClick={(event) => {
                event.stopPropagation();
                onCreateSession(bot.botId);
              }}
            />
            {canFavoriteSessions && (
              <SessionScopeFilter
                compact
                value={sessionTab}
                onChange={handleSessionScopeChange}
                allCount={allSessionMeta?.total}
                favoriteCount={favoriteSessionMeta?.total}
              />
            )}
          </div>
        )}
        {/* v1.4：展开/收起箭头固定在行最右（操作区之后），不随操作区显隐跳动。 */}
        {bot.chatable &&
          !bot.isAgentCodingBot &&
          (expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          ))}
      </div>

      {expanded && bot.chatable && (
        /* v1.4：展开会话区容器整体缩进 + 树形连接线（1px 竖线贴会话区
           左缘起点，颜色取 border 全值）——贴边避免线两侧空白造成的割裂感。
           验收微调：去掉缩进区极浅底色，消除条带感。 */
        /* 验收微调：树形干线改由每行 SessionCard 自带（含末行截断），容器不再渲染贯穿线。 */
        <div aria-label={`Bot会话列表：${bot.displayName}`} className="pl-6">
          {/* 验收微调：去掉 overflow-hidden——行内拐角导轨需向缩进区延伸，不能被裁剪。 */}
          <div>
            {sessionTab === 'all' && allSessionMeta?.error ? (
              <ListErrorState message={allSessionMeta.error} onRetry={() => void onReloadBot?.(bot.botId)} />
            ) : sessionTab === 'favorite' && favoriteSessionMeta?.error ? (
              <ListErrorState message={favoriteSessionMeta.error} onRetry={() => void onLoadFavorites(bot.botId)} />
            ) : sessionTab === 'all' && sessions === undefined ? (
              <div>
                {[1, 2, 3].map((i) => (
                  <Skeleton.Block
                    key={i}
                    className="h-[60px] w-full rounded-none border-b border-border last:border-b-0"
                  />
                ))}
              </div>
            ) : sessionTab === 'favorite' && favoriteSessions === undefined ? (
              <div className="px-3 py-5">
                <span className="text-xs text-muted-foreground">正在加载收藏会话…</span>
              </div>
            ) : (sessionTab === 'all' ? sessions ?? [] : favoriteSessions ?? []).length === 0 ? (
              <div className="px-3 py-5">
                <span className="text-xs text-muted-foreground">
                  {sessionTab === 'favorite' ? '暂无已收藏会话' : '暂无会话'}
                </span>
              </div>
            ) : (
              (sessionTab === 'all' ? sessions ?? [] : favoriteSessions ?? []).map((s) => (
                <BotSessionItem
                  key={s.sessionId}
                  session={s}
                  selected={selectedBotSessionId === s.sessionId}
                  onSelect={(sid) => onSelectSession(bot.botId, sid)}
                  onDelete={(sid) => onDeleteSession(bot.botId, sid)}
                  onRename={(sid, title) => onRenameSession(bot.botId, sid, title)}
                  onClearContext={(sid) => onClearSessionContext(bot.botId, sid)}
                  favorite={s.favorite}
                  showFavorite={canFavoriteSessions}
                  onToggleFavorite={(sid) => onToggleFavorite(bot.botId, sid)}
                />
              ))
            )}
          </div>
          {((sessionTab === 'all' ? allSessionMeta : favoriteSessionMeta)?.hasMore ?? false) && (
            /* 验收微调：加载更多行去背景与分割线，仅保留整体留白。 */
            <div className="flex justify-center px-4 pb-2 pt-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={(sessionTab === 'all' ? allSessionMeta : favoriteSessionMeta)?.isLoadingMore}
                onClick={(event) => {
                  event.stopPropagation();
                  void onLoadMoreSessions?.(bot.botId, sessionTab);
                }}
                className="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground hover:bg-accent"
              >
                {(sessionTab === 'all' ? allSessionMeta : favoriteSessionMeta)?.isLoadingMore
                  ? '正在加载…'
                  : '加载更多会话'}
              </Button>
            </div>
          )}
          {(sessionTab === 'all' ? allSessionMeta : favoriteSessionMeta)?.loadMoreError && (
            <ListErrorState
              message={(sessionTab === 'all' ? allSessionMeta : favoriteSessionMeta)?.loadMoreError ?? ''}
              onRetry={() => void onLoadMoreSessions?.(bot.botId, sessionTab)}
            />
          )}
        </div>
      )}
    </div>
  );
});
