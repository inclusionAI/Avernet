// 协作群侧栏 v2 筛选弹层（dmore artboard-010 搜索行右侧漏斗 icon）。
// 承载既有双维筛选值域：协作群类型（全部/自由聊天/任务协作/自定义协同）+ 参与方式
//（固定群成员/仅参与临时会话）；形态对齐对话页已验收的 ConversationOriginFilter：
// ListFilter icon + 灰轨 Segmented，激活态蓝色高亮 + 圆点提示。
import { IconButton, Popover, PopoverContent, PopoverTrigger, Segmented } from '@/components/ui';
import { cn } from '@/utils/cn';
import { ListFilter } from 'lucide-react';
import { useState } from 'react';
import type { KindFilter, Membership } from './GroupSidebarSearchRow';

export type { KindFilter, Membership };

const KIND_OPTIONS: { value: KindFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'free_chat', label: '自由聊天' },
  { value: 'task_master_slave', label: '任务协作' },
  { value: 'task_dag', label: '自定义协同' },
];

const MEMBERSHIP_OPTIONS: { value: Membership; label: string }[] = [
  { value: 'direct', label: '固定群成员' },
  { value: 'session_only', label: '仅参与临时会话' },
];

// 选项胶囊样式同 ConversationOriginFilter（稿面 DOM 实测 26px 高/圆角 6）。
const OPTION_BASE = 'flex-none h-[26px] rounded-md px-[9px] text-[13px] font-normal tracking-[-0.15px]';
const OPTION_ACTIVE = 'text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.1)]';
const OPTION_INACTIVE = 'text-muted-foreground hover:text-foreground';

export interface GroupSidebarFilterPopoverProps {
  kindFilter: KindFilter;
  onKindFilterChange: (value: KindFilter) => void;
  membership: Membership;
  onMembershipChange: (value: Membership) => void;
}

/** 群列表筛选：漏斗 icon 触发，两组 Segmented（类型 + 参与方式），选中即回调并关闭。 */
export function GroupSidebarFilterPopover({
  kindFilter,
  onKindFilterChange,
  membership,
  onMembershipChange,
}: GroupSidebarFilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const hasActiveFilters = membership !== 'direct' || kindFilter !== 'all';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label="筛选协作群"
          aria-pressed={hasActiveFilters}
          size="sm"
          icon={
            <span className="relative flex items-center justify-center">
              <ListFilter className="h-4 w-4" aria-hidden="true" />
              {hasActiveFilters && (
                <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
              )}
            </span>
          }
          ariaLabel={hasActiveFilters ? '筛选协作群（已有生效筛选）' : '筛选协作群'}
          className={cn(
            'h-8 w-8 rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground',
            hasActiveFilters && 'text-primary hover:text-primary',
          )}
        />
      </PopoverTrigger>
      <PopoverContent
        forceMount
        align="end"
        // 关闭时仍挂载供状态断言，显式 hidden 属性收起（jsdom 下 class 不参与可访问性判定）。
        hidden={!open}
        className={cn(
          // 宽度随内容自适应（覆盖基线 w-72）：类型组 4 个胶囊总宽（约 270px）超过固定
          // 240px 会溢出气泡（2026-10-12 用户反馈），两组轨道取宽者即气泡宽。
          'flex w-auto flex-col gap-4 rounded-lg px-[13px] py-[17px]',
          'shadow-[0_9px_28px_8px_rgba(0,10,26,0.04),0_6px_16px_0px_rgba(0,10,26,0.07),0_3px_6px_-4px_rgba(155,166,185,0.11)]',
        )}
        onClick={(event) => event.stopPropagation()}
      >
        <section>
          <p className="m-0 text-xs font-normal leading-4 text-content-soft">协作群类型</p>
          <Segmented
            className="mt-1 w-fit p-[3px]"
            aria-label="协作群类型"
            value={kindFilter}
            options={KIND_OPTIONS}
            onChange={(next) => {
              onKindFilterChange(next);
              setOpen(false);
            }}
            activeOptionClassName={cn(OPTION_BASE, OPTION_ACTIVE)}
            inactiveOptionClassName={cn(OPTION_BASE, OPTION_INACTIVE)}
          />
        </section>
        <section>
          <p className="m-0 text-xs font-normal leading-4 text-content-soft">参与方式</p>
          <Segmented
            className="mt-1 w-fit p-[3px]"
            aria-label="参与方式"
            value={membership}
            options={MEMBERSHIP_OPTIONS}
            onChange={(next) => {
              onMembershipChange(next);
              setOpen(false);
            }}
            activeOptionClassName={cn(OPTION_BASE, OPTION_ACTIVE)}
            inactiveOptionClassName={cn(OPTION_BASE, OPTION_INACTIVE)}
          />
        </section>
      </PopoverContent>
    </Popover>
  );
}