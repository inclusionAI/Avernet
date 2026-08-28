import { Button, Empty, Input, Segmented, Skeleton } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { GroupKind, GroupView, SessionView } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronUp, Search } from 'lucide-react';
import { useState } from 'react';
import { WorkspaceActionButton } from '../WorkspaceActionButton';
import { GroupItem } from './GroupItem';

export type KindFilter = 'all' | GroupKind;
export type SortMode = 'lastActivity' | 'createdAt';
export type SessionTab = 'all' | 'favorite';
export type Membership = 'direct' | 'session_only';

const KIND_LABELS: Record<KindFilter, string> = {
  all: '全部',
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

export interface GroupSidebarProps {
  view: 'chat' | 'group';
  onViewChange: (v: 'chat' | 'group') => void;
  /** 当前身份可见视图；Bot 仅协作群时不再渲染「会话」切换项。 */
  availableViews?: WorkspaceView[];
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

const KIND_OPTIONS: KindFilter[] = ['all', 'free_chat', 'task_master_slave', 'task_dag'];

const kindChipClass = (active: boolean) =>
  cn(
    'h-auto flex-1 whitespace-nowrap rounded-full border-0 px-1 py-1 text-xs hover:bg-transparent',
    active
      ? 'bg-[var(--color-primary-soft)] font-medium text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)] hover:text-[var(--color-primary)]'
      : 'bg-transparent font-normal text-[var(--color-muted)]',
  );

/** 二级协作群列表内容本体（不含 <aside> 外壳）。由内流 GroupSidebar 与 <lg 抽屉复用，保证两处一致。 */
export function GroupSidebarList(props: GroupSidebarProps) {
  const [kindFilterOpen, setKindFilterOpen] = useState(false);
  const {
    view,
    onViewChange,
    availableViews: availableViewsProp,
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
  const tabOptions = availableViews.map((v) => ({ value: v, label: v === 'chat' ? '会话' : '协作群' }));

  return (
    <>
      <div className="mb-2 flex items-center gap-2">
        <Segmented<'chat' | 'group'>
          className="min-w-0 flex-1"
          value={view}
          options={tabOptions}
          onChange={onViewChange}
        />
        <WorkspaceActionButton onAddFriend={onAddFriend} onCreateGroup={onCreateGroup} />
      </div>
      {/* 角色：群成员 / 会话成员 二元开关，映射 listGroups membership 参数 */}
      <div className="mb-2 flex items-center gap-2 pl-1">
        <span className="shrink-0 text-xs text-[var(--color-fg)]">角色</span>
        <Segmented<Membership>
          className="min-w-0 flex-1"
          value={membership}
          options={[
            { value: 'direct', label: '群成员' },
            { value: 'session_only', label: '会话成员' },
          ]}
          onChange={onMembershipChange}
        />
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={kindFilterOpen}
          aria-controls="group-kind-filter"
          onClick={() => setKindFilterOpen((open) => !open)}
          className={cn(
            'shrink-0 gap-1 rounded-lg',
            kindFilterOpen
              ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)]'
              : 'border border-[var(--color-primary)] text-[var(--color-fg)] hover:bg-[var(--color-primary-soft)]',
          )}
        >
          群类型
          {kindFilterOpen ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          )}
        </Button>
      </div>

      {/* Kind 过滤：四选一 radio group（用 Button 渲染以满足 a11y 角色） */}
      {kindFilterOpen && (
        <div
          id="group-kind-filter"
          role="radiogroup"
          aria-label="协作群类型过滤"
          className="mb-2 flex items-center gap-1 pl-1"
        >
          <span className="mr-1 shrink-0 text-xs text-[var(--color-fg)]">群类型</span>
          {KIND_OPTIONS.map((kind) => (
            <Button
              key={kind}
              variant="ghost"
              size="sm"
              role="radio"
              aria-checked={kindFilter === kind}
              onClick={() => onKindFilterChange(kind)}
              className={kindChipClass(kindFilter === kind)}
            >
              {KIND_LABELS[kind]}
            </Button>
          ))}
        </div>
      )}

      {/* 搜索 */}
      <div className="relative my-2">
        <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted)]" />
        <Input
          className="pl-9"
          value={groupSearchText}
          onChange={(event) => onSearchTextChange(event.target.value)}
          placeholder="搜索协作群名称"
          aria-label="搜索协作群"
        />
      </div>

      <div className="mb-2 flex items-center justify-end gap-2">
        {sessionSearchText && (
          <Button variant="ghost" size="sm" onClick={onClearSessionFilter}>
            清除
          </Button>
        )}
      </div>
      {sessionSearchText !== '' && (
        <div className="relative mb-2">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted)]" />
          <Input
            className="pl-9"
            value={sessionSearchText}
            onChange={(event) => onSessionSearchTextChange(event.target.value)}
            placeholder="搜索会话标题"
            aria-label="搜索会话"
          />
        </div>
      )}

      {/* 列表区 */}
      {isLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton.Block key={i} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        groupSearchText !== '' || kindFilter !== 'all' ? (
          <div className="flex min-h-72 flex-col items-center justify-center px-6 text-center">
            <p className="m-0 text-base font-medium text-[var(--color-fg)]">没有匹配的协作群</p>
            <p className="mt-2 text-sm text-[var(--color-muted)]">试试调整搜索词或筛选条件。</p>
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
              />
            );
          })}
        </div>
      )}
    </>
  );
}

/** 内流协作群列表外壳。≥lg 在流内；<lg hidden，由 Workspace 抽屉呈现同一 GroupSidebarList。 */
export function GroupSidebar(props: GroupSidebarProps) {
  return (
    <aside className="app-scrollbar hidden w-[340px] shrink-0 flex-col overflow-y-auto border-r border-[var(--color-border)] bg-[var(--color-panel-muted)] p-2 lg:flex">
      <GroupSidebarList {...props} />
    </aside>
  );
}
