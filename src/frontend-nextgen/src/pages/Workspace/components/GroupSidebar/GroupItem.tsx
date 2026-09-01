import { Button, Card, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { GroupView, SessionView } from '@/domain/collaboration/types';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { cn } from '@/utils/cn';
import { LoaderCircle, MoreHorizontal, Plus, Users } from 'lucide-react';
import React from 'react';
import { AvatarTile } from '../AvatarTile';
import { formatMonthDayTime } from '../SessionCard';
import { SessionTabs, type SessionTabValue } from '../SessionTabs';
import { SessionItem } from './SessionItem';

export type SessionTab = SessionTabValue;

interface GroupItemProps {
  group: GroupView;
  /** 是否展开会话列表（群默认收起，展开后才按需加载会话）。 */
  expanded: boolean;
  /** 会话列表；undefined 表示首次拉取尚未完成（展示骨架屏）。 */
  sessions: SessionView[] | undefined;
  sessionTab: SessionTab;
  onSessionTabChange: (t: SessionTab) => void;
  favoriteSessionIds: string[];
  selectedSessionId: string | null;
  onSelectGroup: (groupId: string) => void;
  onToggleGroupExpanded: (groupId: string) => void;
  /** 选中会话——需要带 groupId：跨群点选时上层要同步切换选中群。 */
  onSelectSession: (groupId: string, sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onCreateSession: (groupId: string) => void;
  onManageGroup: (groupId: string) => void;
  /** 会话管理：打开对应会话的管理面板。 */
  onManageSession: (groupId: string, sessionId: string) => void;
  /** 后端分页返回的总会话数；未提供时回退到已加载数量。 */
  totalSessionCount?: number;
  hasMoreSessions: boolean;
  isLoadingMoreSessions: boolean;
  onLoadMoreSessions: () => Promise<void>;
}

const KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

export const GroupItem = React.memo(function GroupItem({
  group,
  expanded,
  sessions,
  sessionTab,
  onSessionTabChange,
  favoriteSessionIds,
  selectedSessionId,
  onSelectGroup,
  onToggleGroupExpanded,
  onSelectSession,
  onToggleFavorite,
  onCreateSession,
  onManageGroup,
  onManageSession,
  totalSessionCount,
  hasMoreSessions,
  isLoadingMoreSessions,
  onLoadMoreSessions,
}: GroupItemProps) {
  // 点击群卡片：选中该群并切换会话列表的展开/收起，使整个卡片可折叠。
  const handleCardClick = () => {
    // 已选中该群时仅切换折叠态，避免 selectGroup 重置 selectedSessionId 导致闪烁。
    const isSelected = useWorkspaceStore.getState().selectedGroupId === group.groupId;
    if (!isSelected) onSelectGroup(group.groupId);
    onToggleGroupExpanded(group.groupId);
  };
  const handleCreateSession = (e: React.MouseEvent) => {
    e.stopPropagation();
    onCreateSession(group.groupId);
  };
  // 会话列表加载中时，列表与收藏计数为空占位。
  const loaded = sessions !== undefined;
  const safeSessions = sessions ?? [];
  // 每群独立的 全部会话/收藏 过滤：仅过滤本群会话列表。
  const visibleSessions =
    sessionTab === 'favorite' ? safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)) : safeSessions;
  // 群创建时间(MM/dd HH:mm),参照会话卡片 dateText 的展示格式;无法解析时为空串 → 不渲染。
  const createdTime = formatMonthDayTime(group.createdAt);

  return (
    <>
      <div className="space-y-2">
        <Card
          className={cn(
            'flex items-center gap-1 rounded-lg p-1 transition-colors',
            expanded && 'border-primary bg-primary/5',
          )}
        >
          <Button
            variant="ghost"
            aria-expanded={expanded}
            onClick={handleCardClick}
            className="flex h-auto min-w-0 flex-1 items-center justify-start gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-transparent"
          >
            <AvatarTile label={group.name} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1">
                <TooltipProvider delayDuration={300}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{group.name}</span>
                    </TooltipTrigger>
                    <TooltipContent>{group.name}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <div className="mt-0.5 flex min-w-0 items-center gap-1.5 truncate text-[11px] text-muted-foreground">
                <span className="shrink-0">{KIND_LABEL[group.kind]}</span>
                <span aria-hidden="true" className="text-muted-foreground/50">
                  ·
                </span>
                {group.membership === 'session_only' && <span className="shrink-0 text-primary">临时会话成员</span>}
                {group.membership === 'session_only' && (
                  <span aria-hidden="true" className="text-muted-foreground/50">
                    ·
                  </span>
                )}
                <span className="shrink-0">{group.isPublic ? '公开' : '私有'}</span>
                <span aria-hidden="true" className="text-muted-foreground/50">
                  ·
                </span>
                <span className="inline-flex min-w-0 items-center gap-0.5 truncate">
                  <Users className="h-3 w-3 shrink-0" />
                  {group.participantCount} 个成员
                </span>
                {createdTime ? (
                  <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{createdTime}</span>
                ) : null}
              </div>
            </div>
          </Button>
          <div className="flex shrink-0 items-center gap-0.5">
            <IconButton label="新建会话" size="sm" icon={<Plus className="h-4 w-4" />} onClick={handleCreateSession} />
            <IconButton
              label="管理协作群"
              size="sm"
              icon={<MoreHorizontal className="h-4 w-4" />}
              onClick={() => onManageGroup(group.groupId)}
            />
          </div>
        </Card>

        {expanded && (
          <div className="pl-3">
            <SessionTabs
              className="w-full border-b border-border/70 pb-1"
              value={sessionTab}
              showCount
              countFormat="suffix"
              allCount={totalSessionCount ?? safeSessions.length}
              favoriteCount={safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)).length}
              onChange={onSessionTabChange}
            />
            <div className="mt-2 overflow-hidden rounded-lg border border-border bg-card">
              {!loaded ? (
                <div>
                  {[1, 2, 3].map((i) => (
                    <Skeleton.Block
                      key={i}
                      className="h-14 w-full rounded-none border-b border-border last:border-b-0"
                    />
                  ))}
                </div>
              ) : visibleSessions.length === 0 ? (
                <div className="px-3 py-4">
                  <span className="text-xs text-muted-foreground">
                    {sessionTab === 'favorite' ? '暂无已收藏会话' : '暂无协作群会话'}
                  </span>
                </div>
              ) : (
                visibleSessions.map((session) => (
                  <SessionItem
                    key={session.sessionId}
                    session={session}
                    favorite={favoriteSessionIds.includes(session.sessionId)}
                    selected={selectedSessionId === session.sessionId}
                    onSelectSession={(sessionId) => onSelectSession(group.groupId, sessionId)}
                    onToggleFavorite={onToggleFavorite}
                    onManageSession={(sessionId) => onManageSession(group.groupId, sessionId)}
                  />
                ))
              )}
            </div>
            {hasMoreSessions && (
              <div className="mt-2 flex justify-center">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={isLoadingMoreSessions}
                  onClick={(event) => {
                    event.stopPropagation();
                    void onLoadMoreSessions();
                  }}
                  className="h-8 rounded-md px-3 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  {isLoadingMoreSessions && <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                  {isLoadingMoreSessions ? '正在加载…' : '加载更多'}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
});
