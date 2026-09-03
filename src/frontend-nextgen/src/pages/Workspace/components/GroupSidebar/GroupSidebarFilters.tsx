import { Button, Input } from '@/components/ui';
import type { GroupKind } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { Check, Filter, Search } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

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
    'h-7 flex-none whitespace-nowrap rounded-none border-0 px-0 text-xs',
    active
      ? 'bg-transparent font-medium text-primary hover:bg-transparent hover:text-primary'
      : 'bg-transparent font-normal text-muted-foreground hover:bg-transparent hover:text-foreground',
  );

interface GroupSidebarFiltersProps {
  groupSearchText: string;
  onSearchTextChange: (value: string) => void;
  kindFilter: KindFilter;
  onKindFilterChange: (value: KindFilter) => void;
  membership: Membership;
  onMembershipChange: (value: Membership) => void;
}

export function GroupSidebarFilters({
  groupSearchText,
  onSearchTextChange,
  kindFilter,
  onKindFilterChange,
  membership,
  onMembershipChange,
}: GroupSidebarFiltersProps) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterPanelId = `group-filters-${useId().replace(/:/g, '')}`;
  const filterRegionRef = useRef<HTMLDivElement>(null);
  const hasActiveFilters = membership !== 'direct' || kindFilter !== 'all';

  useEffect(() => {
    if (!filtersOpen) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (!filterRegionRef.current?.contains(event.target as Node)) setFiltersOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFiltersOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [filtersOpen]);

  const handleMembershipChange = (nextMembership: Membership) => {
    onMembershipChange(nextMembership);
    setFiltersOpen(false);
  };
  const handleKindFilterChange = (nextKind: KindFilter) => {
    onKindFilterChange(nextKind);
    setFiltersOpen(false);
  };

  return (
    <div ref={filterRegionRef} className="my-2">
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
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={filtersOpen}
          aria-pressed={hasActiveFilters}
          aria-controls={filterPanelId}
          aria-label="筛选"
          onClick={() => setFiltersOpen((open) => !open)}
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
      </div>

      {filtersOpen && (
        <div id={filterPanelId} className="mt-2 space-y-2 border-y border-border/80 bg-muted px-[18px] py-3">
          <div
            className="grid min-h-7 min-w-0 grid-cols-[4rem_minmax(0,1fr)] items-center gap-1"
            role="radiogroup"
            aria-label="协作群类型"
          >
            <p className="whitespace-nowrap text-[11px] font-medium text-muted-foreground">协作群类型</p>
            <div className="flex min-w-0 flex-nowrap gap-x-2 overflow-x-auto scrollbar-hide">
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
          <div
            className="grid min-h-7 min-w-0 grid-cols-[4rem_minmax(0,1fr)] items-center gap-1"
            role="radiogroup"
            aria-label="协作群参与方式"
          >
            <p className="whitespace-nowrap text-[11px] font-medium text-muted-foreground">参与方式</p>
            <div className="flex min-w-0 flex-nowrap gap-x-2 overflow-x-auto scrollbar-hide">
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
        </div>
      )}
    </div>
  );
}
