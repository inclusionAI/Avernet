// 协作群侧栏 v2 搜索行（dmore artboard-010 Container 295x48）：h-8 搜索框「请输入关键词」+
// 右侧筛选（漏斗 x=211）与发起协作（+ x=251）两个 32px 图标按钮，替代旧 GroupSidebarFilters。
// 收口侧栏仍走 ResizableWorkspaceSidebar 边缘 chevron（与对话侧栏同一收起通道）。
import type { GroupKind } from '@/domain/collaboration/types';
import { Input } from '@/components/ui';
import { Search } from 'lucide-react';
import { WorkspaceActionButton } from '../WorkspaceActionButton';
import { GroupSidebarFilterPopover } from './GroupSidebarFilterPopover';

export type KindFilter = 'all' | GroupKind;
export type Membership = 'direct' | 'session_only';

export interface GroupSidebarSearchRowProps {
  groupSearchText: string;
  onSearchTextChange: (value: string) => void;
  kindFilter: KindFilter;
  onKindFilterChange: (value: KindFilter) => void;
  membership: Membership;
  onMembershipChange: (value: Membership) => void;
  /** 「+」发起协作：打开既有 CreateGroupModal 链路。 */
  onCreateGroup: () => void;
}

/** 搜索 + 筛选 + 发起协作：三控件一行，间距 8px、整体与对话侧栏同一 sticky 惯例。 */
export function GroupSidebarSearchRow({
  groupSearchText,
  onSearchTextChange,
  kindFilter,
  onKindFilterChange,
  membership,
  onMembershipChange,
  onCreateGroup,
}: GroupSidebarSearchRowProps) {
  return (
    <div className="flex items-center gap-2 px-4 pb-1 pt-1">
      <div className="relative min-w-0 flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-content-icon" />
        <Input
          className="h-8 rounded-lg border border-input bg-background pl-8 text-xs text-foreground placeholder:text-content-hint"
          value={groupSearchText}
          onChange={(event) => onSearchTextChange(event.target.value)}
          placeholder="请输入关键词"
          aria-label="搜索协作群"
        />
      </div>
      <GroupSidebarFilterPopover
        kindFilter={kindFilter}
        onKindFilterChange={onKindFilterChange}
        membership={membership}
        onMembershipChange={onMembershipChange}
      />
      <WorkspaceActionButton onCreateGroup={onCreateGroup} />
    </div>
  );
}