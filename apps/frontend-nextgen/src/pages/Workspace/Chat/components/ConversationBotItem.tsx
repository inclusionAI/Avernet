// 管理 Bot / 好友 Bot 通用行:展开区 + 行内动作(归属筛选 / 新建会话)。
// 展开区按 section/origin 分流:
// - managed+mine / friend:交互式 Session 列表(选中 / 新建 / 加载更多走 Hook 回调);
// - managed+others:好友用户分组只读视图(分组渲染在 ConversationFriendGroup)。
// 允许多个 Bot 同时展开(展开状态由 Store 按 botId 记录,行内不做互斥)。
import { Badge, Button, IconButton, Skeleton } from '@/components/ui';
import { getBotEngineLabel, supportsBotSessionFavorites } from '@/domain/botEngine';
import { getBotTypeLabel } from '@/domain/botType';
import type {
  ConversationBotSection,
  ConversationFriendDirectoryState,
  ConversationFriendGroupView,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import React from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationFriendGroup } from './ConversationFriendGroup';
import { ConversationOriginFilter } from './ConversationOriginFilter';
import { ConversationSessionList } from './ConversationSessionList';

export interface ConversationBotItemOthers {
  directory: ConversationFriendDirectoryState;
  /** Store 中是否已有 others 缓存(false 表示尚未加载过,不能判空)。 */
  hasCache: boolean;
  /** 按好友目录顺序排序的分组视图。 */
  friendGroups: ConversationFriendGroupView[];
  /** 跨好友分组的当前选中只读 Session。 */
  selectedSessionId: string | null;
  /** 展开的好友用户(key 为纯用户 ID),来自 Store expandedFriendUserIdsByBotId。 */
  expandedFriendUserIds: Record<string, true>;
  onToggleFriend(friendUserId: string): void;
  onSelectSession(sessionId: string, friendUserId: string): void;
  onRetryDirectory(): void;
  onRetryFriendSessions(friendUserId: string): void;
  onLoadMoreFriendSessions(friendUserId: string): void;
}

export interface ConversationBotItemProps {
  bot: ChatBotView;
  section: ConversationBotSection;
  expanded: boolean;
  selected: boolean;
  /** 当前 Bot 下选中的 Session(跨 origin 高亮)。 */
  selectedSessionId: string | null;
  origin: ConversationOrigin;
  /** 生效读取范围(managed mine 用;friend 恒 all)。 */
  scope: ConversationSessionScope;
  /** mine / friend 交互会话列表;undefined 视为未加载(不判空)。 */
  sessions?: ConversationSessionListState;
  /** 仅 managed 传入:others 好友分组区数据。 */
  others?: ConversationBotItemOthers;
  onToggle(): void;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onLoadMore(): void;
  onToggleFavorite(sessionId: string): void;
  isFavoritePending(sessionId: string): boolean;
  onOpenBotWorkshop(): void;
}

// AC-9:全部好友分组均成功且零会话(整组隐藏)→ 整区空态;
// unloaded/loading/error 的分组隐藏 ≠ 空,不算空。
function allFriendGroupsEmpty(groups: ConversationFriendGroupView[]): boolean {
  return (
    groups.length > 0 &&
    groups.every((group) => group.state === 'loaded' && group.sessions.items.length === 0 && !group.sessions.error)
  );
}

/** origin=others 展开区:好友目录 / 好友分组的 loading / error / empty 状态。 */
function OthersArea({ others }: { others?: ConversationBotItemOthers }) {
  if (!others || !others.hasCache || others.directory.loading) {
    return (
      <div className="py-1">
        {[1, 2].map((i) => (
          <Skeleton.Block key={i} className="mx-4 h-12 rounded-lg" />
        ))}
      </div>
    );
  }
  if (others.directory.error) {
    return <ListErrorState message={others.directory.error} onRetry={others.onRetryDirectory} />;
  }
  if (others.friendGroups.length === 0 || allFriendGroupsEmpty(others.friendGroups)) {
    return <div className="px-4 py-3 text-xs text-muted-foreground">暂无他人发起的会话</div>;
  }
  return (
    <>
      {others.friendGroups.map((group) => (
        <ConversationFriendGroup
          key={group.friend.userId}
          group={group}
          expanded={others.expandedFriendUserIds[group.friend.userId] === true}
          selectedSessionId={others.selectedSessionId}
          onToggle={() => others.onToggleFriend(group.friend.userId)}
          onSelectSession={(sessionId) => others.onSelectSession(sessionId, group.friend.userId)}
          onRetry={() => others.onRetryFriendSessions(group.friend.userId)}
          onLoadMore={() => void others.onLoadMoreFriendSessions(group.friend.userId)}
        />
      ))}
    </>
  );
}

export const ConversationBotItem = React.memo(function ConversationBotItem({
  bot,
  section,
  expanded,
  selected,
  selectedSessionId,
  origin,
  scope,
  sessions,
  others,
  onToggle,
  onOriginChange,
  onScopeChange,
  onCreateSession,
  onSelectSession,
  onLoadMore,
  onToggleFavorite,
  isFavoritePending,
  onOpenBotWorkshop,
}: ConversationBotItemProps) {
  const isManaged = section === 'managed';
  const isOthers = isManaged && origin === 'others';
  const interactive = !bot.isAgentCodingBot && bot.chatable;
  const engineLabel = getBotEngineLabel(bot.engine);
  const typeLabel = getBotTypeLabel(bot.botType);
  const canFavorite = supportsBotSessionFavorites(bot.engine);
  const filterActive = isManaged && (origin === 'others' || (canFavorite && scope === 'favorite'));
  const handleToggle = () => {
    // AgentCoding Bot 不建立侧栏会话,引导到 Bot 工坊使用。
    if (bot.isAgentCodingBot) {
      onOpenBotWorkshop();
      return;
    }
    if (!bot.chatable) return;
    onToggle();
  };

  return (
    <div>
      <div
        className={cn(
          'group relative flex min-h-14 items-center gap-2 px-4 py-2.5 transition-colors',
          selected || expanded ? 'bg-muted' : 'hover:bg-accent/50',
        )}
      >
        {(selected || expanded) && (
          <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r-sm bg-primary" />
        )}
        <Button
          variant="ghost"
          aria-label={bot.displayName}
          aria-expanded={expanded}
          aria-current={selected ? 'page' : undefined}
          onClick={handleToggle}
          className={cn(
            'flex h-auto min-w-0 flex-1 items-center justify-start gap-3 rounded-none px-0 py-1 text-left hover:bg-transparent',
            !interactive && 'cursor-not-allowed opacity-50',
          )}
        >
          <AvatarTile
            src={bot.avatarUrl}
            label={bot.displayName}
            className={selected || expanded ? 'bg-primary/15 text-primary ring-1 ring-primary/30' : undefined}
            fallbackContent={<span className="text-[10px] font-semibold tracking-[0.08em]">BOT</span>}
          />
          <div className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-foreground">{bot.displayName}</span>
            <div className="mt-1 flex min-w-0 items-center gap-1 truncate text-xs leading-4 text-muted-foreground group-hover:pr-24">
              {engineLabel && (
                <Badge tone="primary" className="shrink-0 rounded px-1.5 py-0 text-[10px] leading-4">
                  {engineLabel}
                </Badge>
              )}
              {typeLabel && (
                <Badge tone="primary" className="shrink-0 rounded px-1.5 py-0 text-[10px] leading-4">
                  {typeLabel}
                </Badge>
              )}
            </div>
          </div>
        </Button>
        {interactive && (
          <div
            className={cn(
              'absolute inset-y-0 right-[30px] z-10 flex items-center gap-0.5 pl-5',
              'mask-[linear-gradient(to_right,transparent,black_20px)]',
              selected || expanded ? 'sidebar-actions-cover-selected' : 'sidebar-actions-cover-hover',
              'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
              (selected || expanded || filterActive) && 'opacity-100',
            )}
          >
            {isManaged && (
              <ConversationOriginFilter
                botId={bot.botId}
                origin={origin}
                scope={scope}
                supportsFavorites={canFavorite}
                onOriginChange={onOriginChange}
                onScopeChange={onScopeChange}
              />
            )}
            {!isOthers && (
              <IconButton
                label="新建会话"
                size="sm"
                icon={<Plus className="h-3.5 w-3.5" />}
                className="h-6 w-6 rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
                onClick={(event) => {
                  event.stopPropagation();
                  onCreateSession();
                }}
              />
            )}
          </div>
        )}
        {interactive &&
          (expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          ))}
      </div>
      {expanded && interactive && (
        <div aria-label={`会话列表：${bot.displayName}`} className="pl-6">
          {isOthers ? (
            <OthersArea others={others} />
          ) : (
            <ConversationSessionList
              sessions={sessions}
              selectedSessionId={selectedSessionId}
              onSelectSession={onSelectSession}
              onLoadMore={onLoadMore}
              onToggleFavorite={canFavorite ? onToggleFavorite : undefined}
              isFavoritePending={isFavoritePending}
            />
          )}
        </div>
      )}
    </div>
  );
});
