import { Badge, Card, IconButton, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { GroupView, SessionView } from '@/domain/collaboration/types';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Plus, Users } from 'lucide-react';
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
  const favoriteCount = safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)).length;
  // 群创建时间(MM/dd HH:mm),参照会话卡片 dateText 的展示格式;无法解析时为空串 → 不渲染。
  const createdTime = formatMonthDayTime(group.createdAt);

  return (
    <>
      <div className="space-y-2">
        <Card
          role="button"
          tabIndex={0}
          aria-expanded={expanded}
          onClick={handleCardClick}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              handleCardClick();
            }
          }}
          className={cn(
            'flex cursor-pointer items-center gap-3 px-3 py-3 transition-colors',
            expanded && 'border-[var(--color-primary)] bg-[var(--color-primary-soft)]',
          )}
        >
          <AvatarTile label={group.name} />
          <div className="min-w-0 flex-1">
            {/* 第一行:群名称(左,可截断) + 新建/管理操作(右),与会话卡片标题行 + trailing 一致。 */}
            <div className="flex items-center gap-1">
              <TooltipProvider delayDuration={300}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--color-fg)]">
                      {group.name}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{group.name}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="ml-auto flex shrink-0 items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
                <IconButton
                  label="新建会话"
                  size="sm"
                  icon={<Plus className="h-4 w-4" />}
                  onClick={handleCreateSession}
                />
                <IconButton
                  label="管理协作群"
                  size="sm"
                  icon={<MoreHorizontal className="h-4 w-4" />}
                  onClick={(e) => {
                    e.stopPropagation();
                    onManageGroup(group.groupId);
                  }}
                />
              </div>
            </div>
            {/* 第二行:类型/可见性/成员数(左侧) + 创建时间(右侧 ml-auto),参照会话卡片副行 subtitle + date 布局。 */}
            <div className="mt-1 flex items-center gap-1.5">
              <Badge tone="primary">{KIND_LABEL[group.kind]}</Badge>
              {group.isPublic ? <Badge tone="success">公开</Badge> : <Badge tone="neutral">私有</Badge>}
              <span className="inline-flex items-center gap-0.5 text-[11px] text-[var(--color-muted)]">
                <Users className="h-3 w-3" />
                {group.participantCount}
              </span>
              {createdTime ? (
                <span className="ml-auto shrink-0 text-[11px] text-[var(--color-muted)]">{createdTime}</span>
              ) : null}
            </div>
          </div>
        </Card>

        {expanded && (
          <div className="pl-3">
            <SessionTabs
              value={sessionTab}
              allCount={safeSessions.length}
              favoriteCount={favoriteCount}
              onChange={onSessionTabChange}
            />
            <div className="mt-1.5 space-y-1.5">
              {!loaded ? (
                <div className="space-y-1.5 p-1">
                  {[1, 2, 3].map((i) => (
                    <Skeleton.Block key={i} className="h-12 w-full rounded-xl" />
                  ))}
                </div>
              ) : visibleSessions.length === 0 ? (
                <div className="py-1.5">
                  <span className="text-xs text-[var(--color-muted)]">暂无会话</span>
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
          </div>
        )}
      </div>
    </>
  );
});
