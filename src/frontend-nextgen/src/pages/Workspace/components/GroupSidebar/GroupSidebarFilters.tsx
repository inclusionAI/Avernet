import { Button, Input, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import type { GroupKind } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { Check, Filter, Search } from 'lucide-react';
import { useState } from 'react';
import { WorkspaceActionButton } from '../WorkspaceActionButton';

export type KindFilter = 'all' | GroupKind;
export type Membership = 'direct' | 'session_only';

const KIND_LABELS: Record<KindFilter, string> = {
  all: '全部',
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

const KIND_OPTIONS: KindFilter[] = ['all', 'free_chat', 'task_master_slave', 'task_dag'];

const kindChipClass = (active: boolean) =>
  cn(
    'h-7 flex-none whitespace-nowrap rounded-md border-0 px-2 text-xs',
    active
      ? 'bg-background font-medium text-primary shadow-sm hover:bg-background hover:text-primary'
      : 'bg-transparent font-normal text-muted-foreground hover:bg-background/70 hover:text-foreground',
  );

interface GroupSidebarFiltersProps {
  groupSearchText: string;
  onSearchTextChange: (value: string) => void;
  kindFilter: KindFilter;
  onKindFilterChange: (value: KindFilter) => void;
  membership: Membership;
  onMembershipChange: (value: Membership) => void;
  onCreateGroup: () => void;
}

export function GroupSidebarFilters({
  groupSearchText,
  onSearchTextChange,
  kindFilter,
  onKindFilterChange,
  membership,
  onMembershipChange,
  onCreateGroup,
}: GroupSidebarFiltersProps) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const hasActiveFilters = membership !== 'direct' || kindFilter !== 'all';

  const handleMembershipChange = (nextMembership: Membership) => {
    onMembershipChange(nextMembership);
    setFiltersOpen(false);
  };
  const handleKindFilterChange = (nextKind: KindFilter) => {
    onKindFilterChange(nextKind);
    setFiltersOpen(false);
  };

  return (
    <div className="my-2">
      <div className="flex items-center gap-2 px-[18px]">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          {/* 基础 Input 为 h-8（32px 标准）；此侧栏按 PR255 视觉规范与筛选按钮等高（h-9） */}
          <Input
            className="h-9 pl-9"
            value={groupSearchText}
            onChange={(event) => onSearchTextChange(event.target.value)}
            placeholder="搜索协作群名称"
            aria-label="搜索协作群"
          />
        </div>
        <Popover open={filtersOpen} onOpenChange={setFiltersOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={hasActiveFilters}
              aria-label="筛选"
              className={cn(
                'h-9 shrink-0 gap-1 rounded-md border border-input bg-background px-2 text-xs text-muted-foreground hover:border-border hover:bg-accent hover:text-foreground',
                filtersOpen &&
                  'border-primary/30 bg-primary/5 text-primary hover:border-primary/40 hover:bg-primary/10 hover:text-primary',
                hasActiveFilters &&
                  'border-primary/30 bg-primary/5 text-primary hover:border-primary/40 hover:bg-primary/10 hover:text-primary',
              )}
            >
              <Filter className="h-3.5 w-3.5" aria-hidden />
              筛选
              {hasActiveFilters && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-primary" />}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            sideOffset={8}
            className="w-[360px] max-w-[calc(100vw-1rem)] space-y-3 rounded-lg border-border/70 p-3"
          >
            <div className="min-w-0" role="radiogroup" aria-label="协作群类型">
              <p className="mb-1.5 whitespace-nowrap text-xs font-medium text-muted-foreground">协作群类型</p>
              <div className="flex min-w-0 flex-wrap gap-1">
                {KIND_OPTIONS.map((kind) => (
                  <Button
                    key={kind}
                    variant="ghost"
                    size="sm"
                    role="radio"
                    aria-checked={kindFilter === kind}
                    onClick={() => handleKindFilterChange(kind)}
                    className={kindChipClass(kindFilter === kind)}
                  >
                    {kindFilter === kind && <Check className="h-3 w-3" aria-hidden />}
                    {KIND_LABELS[kind]}
                  </Button>
                ))}
              </div>
            </div>
            <div className="min-w-0 border-t border-border/60 pt-3" role="radiogroup" aria-label="协作群参与方式">
              <p className="mb-1.5 whitespace-nowrap text-xs font-medium text-muted-foreground">参与方式</p>
              <div className="flex min-w-0 flex-wrap gap-1">
                {(
                  [
                    ['direct', '固定群成员'],
                    ['session_only', '仅参与临时会话'],
                  ] as const
                ).map(([value, label]) => (
                  <Button
                    key={value}
                    variant="ghost"
                    size="sm"
                    role="radio"
                    aria-checked={membership === value}
                    onClick={() => handleMembershipChange(value)}
                    className={kindChipClass(membership === value)}
                  >
                    {membership === value && <Check className="h-3 w-3" aria-hidden />}
                    {label}
                  </Button>
                ))}
              </div>
            </div>
          </PopoverContent>
        </Popover>
        <WorkspaceActionButton onCreateGroup={onCreateGroup} />
      </div>
    </div>
  );
}
