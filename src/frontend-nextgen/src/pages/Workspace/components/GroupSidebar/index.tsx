import { Button, Empty, Input, Segmented, Skeleton } from '@/components/ui';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { GroupView, SessionView } from '@/domain/collaboration/types';
import type { Identity } from '@/services/workspace/workspaceModel';
import { Search } from 'lucide-react';
import { WorkspaceActionButton } from '../WorkspaceActionButton';
import { GroupItem } from './GroupItem';
import { GroupSidebarFilters, type KindFilter, type Membership } from './GroupSidebarFilters';

export type SortMode = 'lastActivity' | 'createdAt';
export type SessionTab = 'all' | 'favorite';

export interface GroupSidebarProps {
  view: 'chat' | 'group';
  onViewChange: (v: 'chat' | 'group') => void;
  /** 当前身份可见视图；Bot 仅协作群时不再渲染「会话」切换项。 */
  availableViews?: WorkspaceView[];
  identities?: Identity[];
  activeIdentityId?: string | null;
  onChangeIdentity?: (id: string) => void;
  onOpenPermissions?: () => void;
  userAvatarUrl?: string;
  groups: GroupView[];
  isLoading: boolean;
  onSelectGroup: (groupId: string) => void;
  groupSearchText: string;
  onSearchTextChange: (v: string) => void;
  kindFilter: KindFilter;
  onKindFilterChange: (k: KindFilter) => void;
  membership: Membership;
  onMembershipChange: (m: Membership) => void;
  sortMode: SortMode;
  onSortModeChange: (m: SortMode) => void;
  expandedGroupIds: Record<string, true>;
  onToggleGroupExpanded: (groupId: string) => void;
  sessionsByGroupId: Record<string, SessionView[]>;
  hasMoreSessionsByGroupId?: Record<string, boolean>;
  totalSessionsByGroupId?: Record<string, number>;
  isLoadingMoreSessionsByGroupId?: Record<string, boolean>;
  onLoadMoreSessions?: (groupId: string) => Promise<void>;
  sessionTabsByGroup: Record<string, SessionTab>;
  onSessionTabForGroup: (groupId: string, tab: SessionTab) => void;
  favoriteSessionIds: string[];
  sessionSearchText: string;
  onSessionSearchTextChange: (v: string) => void;
  selectedSessionId: string | null;
  onSelectSession: (groupId: string, sessionId: string) => void;
  onCreateSession: (groupId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onClearSessionFilter: () => void;
  onCreateGroup: () => void;
  onAddFriend: () => void;
  /** 群管理：打开管理面板。 */
  onManageGroup: (groupId: string) => void;
  /** 会话管理：打开管理面板。 */
  onManageSession: (groupId: string, sessionId: string) => void;
  /** 分享群。 */
  onShareGroup: (groupId: string) => void;
  /** 解散群（已在组件内二次确认，此处直接执行）。 */
  onDissolveGroup: (groupId: string) => void;
}

/** 二级协作群列表内容本体（不含 <aside> 外壳）。由内流 GroupSidebar 与 <lg 抽屉复用，保证两处一致。 */
export function GroupSidebarList(props: GroupSidebarProps) {
  const {
    view,
    onViewChange,
    availableViews: availableViewsProp,
    identities = [],
    activeIdentityId = null,
    onChangeIdentity = () => {},
    onOpenPermissions = () => {},
    userAvatarUrl,
    groups,
    isLoading,
    onSelectGroup,
    groupSearchText,
    onSearchTextChange,
    kindFilter,
    onKindFilterChange,
    membership,
    onMembershipChange,
    expandedGroupIds,
    onToggleGroupExpanded,
    sessionsByGroupId,
    hasMoreSessionsByGroupId = {},
    totalSessionsByGroupId = {},
    isLoadingMoreSessionsByGroupId = {},
    onLoadMoreSessions = async () => {},
    sessionTabsByGroup,
    onSessionTabForGroup,
    favoriteSessionIds,
    sessionSearchText,
    onSessionSearchTextChange,
    selectedSessionId,
    onSelectSession,
    onCreateSession,
    onToggleFavorite,
    onClearSessionFilter,
    onCreateGroup,
    onAddFriend,
    onManageGroup,
    onManageSession,
  } = props;
  const availableViews = availableViewsProp ?? ['chat', 'group'];
  const tabOptions = availableViews.map((v) => ({ value: v, label: v === 'chat' ? '对话' : '协作群' }));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 pb-2">
        <WorkspaceIdentitySelector
          identities={identities}
          activeId={activeIdentityId}
          onChange={onChangeIdentity}
          onOpenPermissions={onOpenPermissions}
          userAvatarUrl={userAvatarUrl}
        />
      </div>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mb-2 flex items-center gap-2">
          <Segmented<'chat' | 'group'>
            className="h-9 min-w-0 flex-1 border border-border/70 bg-muted/70"
            activeOptionClassName="bg-primary/10 font-medium text-primary shadow-none hover:bg-primary/15 hover:text-primary"
            inactiveOptionClassName="bg-background/60 text-foreground/80 hover:bg-background hover:text-foreground"
            value={view}
            options={tabOptions}
            onChange={onViewChange}
          />
          <WorkspaceActionButton onAddFriend={onAddFriend} onCreateGroup={onCreateGroup} />
        </div>
        <GroupSidebarFilters
          groupSearchText={groupSearchText}
          onSearchTextChange={onSearchTextChange}
          kindFilter={kindFilter}
          onKindFilterChange={onKindFilterChange}
          membership={membership}
          onMembershipChange={onMembershipChange}
        />

        {sessionSearchText !== '' && (
          <div className="mb-2 space-y-1">
            <div className="flex items-center justify-end">
              <Button variant="ghost" size="sm" onClick={onClearSessionFilter}>
                清除会话搜索
              </Button>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="pl-9"
                value={sessionSearchText}
                onChange={(event) => onSessionSearchTextChange(event.target.value)}
                placeholder="搜索会话标题"
                aria-label="搜索会话"
              />
            </div>
          </div>
        )}

        {/* 列表区 */}
        {isLoading ? (
          <div className="overflow-hidden rounded-lg border border-border bg-card">
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
            ))}
          </div>
        ) : groups.length === 0 ? (
          groupSearchText !== '' || kindFilter !== 'all' || membership !== 'direct' ? (
            <div className="flex min-h-72 flex-col items-center justify-center px-6 text-center">
              <p className="m-0 text-base font-medium text-foreground">没有匹配的协作群</p>
              <p className="mt-2 text-sm text-muted-foreground">试试调整搜索词或筛选条件。</p>
            </div>
          ) : (
            <Empty
              compact
              title="暂无协作群"
              description="创建协作群后，可在这里与多个 Bot 和用户协同。"
              action={
                <Button size="sm" onClick={onCreateGroup}>
                  发起协作
                </Button>
              }
            />
          )
        ) : (
          <div className="space-y-2">
            {groups.map((group) => {
              const sessions = sessionsByGroupId[group.groupId];
              return (
                <GroupItem
                  key={group.groupId}
                  group={group}
                  expanded={!!expandedGroupIds[group.groupId]}
                  sessions={sessions}
                  sessionTab={sessionTabsByGroup[group.groupId] ?? 'all'}
                  onSessionTabChange={(t) => onSessionTabForGroup(group.groupId, t)}
                  favoriteSessionIds={favoriteSessionIds}
                  selectedSessionId={selectedSessionId}
                  onSelectGroup={onSelectGroup}
                  onToggleGroupExpanded={onToggleGroupExpanded}
                  onSelectSession={onSelectSession}
                  onToggleFavorite={onToggleFavorite}
                  onCreateSession={onCreateSession}
                  onManageGroup={onManageGroup}
                  onManageSession={onManageSession}
                  totalSessionCount={totalSessionsByGroupId[group.groupId]}
                  hasMoreSessions={hasMoreSessionsByGroupId[group.groupId] ?? false}
                  isLoadingMoreSessions={isLoadingMoreSessionsByGroupId[group.groupId] ?? false}
                  onLoadMoreSessions={() => onLoadMoreSessions(group.groupId)}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** 内流协作群列表外壳。≥lg 在流内；<lg hidden，由 Workspace 抽屉呈现同一 GroupSidebarList。 */
export function GroupSidebar(props: GroupSidebarProps) {
  return (
    <aside className="hidden w-[340px] shrink-0 flex-col overflow-hidden border-r border-border bg-muted/30 p-2 lg:flex">
      <GroupSidebarList {...props} />
    </aside>
  );
}
