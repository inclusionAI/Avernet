// 管理一级导航顶部空间切换器。仅管理页 area==='manage' 展示。
// 读 useSpaceContext：当前空间展示 + Popover 下拉（仅已加入）。
// 选中即 switchSpaceContext(id) + 持久化。loading/error/empty 三态。
// 视觉对齐 PRD（Teamclaw_PRD_new/src/components/Layout/Sidebar.tsx）：
//   - 触发器：浅色填充圆角卡片 + border（展开时 border-primary）；左=空间类型图标(User/Users)，右=下拉箭头(展开上旋)
//   - 下拉标题「空间切换」；列表项 图标+空间名，当前项 accent 高亮+primary 文字+右侧 CheckCircle 勾选
//   - 列表仅展示已加入空间，个人空间置顶，团队按 gmtModified 倒序
import { Button, Empty, Popover, PopoverContent, PopoverTrigger, Skeleton } from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { refreshSpaceContext, switchSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import { cn } from '@/utils/cn';
import { CheckCircle, ChevronDown, Loader2, User, Users } from 'lucide-react';
import { useState } from 'react';

function SpaceIcon({ type, className }: { type: Space['spaceType']; className?: string }) {
  // 个人=紫 User(text-brand)，团队=蓝 Users(text-primary)，与 SpaceCard 图标配色一致
  if (type === 'PERSONAL') return <User className={cn('shrink-0 text-brand', className)} aria-hidden />;
  return <Users className={cn('shrink-0 text-[var(--color-primary)]', className)} aria-hidden />;
}

function SpaceAvatar({ type, loading }: { type: Space['spaceType']; loading: boolean }) {
  return (
    <span
      role="img"
      aria-label={loading ? '空间加载中' : `${type === 'PERSONAL' ? '个人空间' : '团队空间'}图标`}
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10"
    >
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden />
      ) : (
        <SpaceIcon type={type} className="h-3.5 w-3.5" />
      )}
    </span>
  );
}

function SpaceRow({ space, active, onSelect }: { space: Space; active: boolean; onSelect: (id: number) => void }) {
  return (
    <button
      type="button"
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-[13px] transition-colors',
        'hover:bg-[var(--color-primary-soft)]',
        active
          ? 'bg-[var(--color-primary-soft)] font-semibold text-[var(--color-primary)]'
          : 'font-normal text-[var(--color-fg)]',
      )}
      onClick={() => onSelect(space.spaceId)}
    >
      <SpaceIcon type={space.spaceType} className="h-4 w-4" />
      <span className="min-w-0 flex-1 truncate">{space.spaceName}</span>
      {active && <CheckCircle className="ml-auto h-3.5 w-3.5 shrink-0 text-[var(--color-primary)]" aria-hidden />}
    </button>
  );
}

export function SpaceSwitcher() {
  const [open, setOpen] = useState(false);
  const currentSpace = useSpaceContext((s) => s.currentSpace);
  const currentSpaceId = useSpaceContext((s) => s.currentSpaceId);
  const spaces = useSpaceContext((s) => s.spaces);
  const loading = useSpaceContext((s) => s.loading);
  const error = useSpaceContext((s) => s.error);

  const onSelect = (id: number) => {
    switchSpaceContext(id);
    setOpen(false);
  };

  // 每次打开气泡都重拉最新空间列表（成员变更/新空间可能发生在上次初始化之后）
  const onOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) void refreshSpaceContext();
  };

  // 列表排序：个人空间置顶，团队按 gmtModified 倒序（与空间管理页一致）
  const ordered = [...spaces].sort((a, b) => {
    if (a.spaceType !== b.spaceType) return a.spaceType === 'PERSONAL' ? -1 : 1;
    return (b.gmtModified || '').localeCompare(a.gmtModified || '');
  });

  let listBody: React.ReactNode;
  if (loading && spaces.length === 0) {
    listBody = (
      <div className="space-y-1 p-1">
        <Skeleton.ListItem />
        <Skeleton.ListItem />
      </div>
    );
  } else if (error && spaces.length === 0) {
    // 仅无缓存数据时才用错误态占位；刷新失败保留旧列表展示
    listBody = <Empty title="加载失败" description={error} compact />;
  } else if (ordered.length === 0) {
    listBody = <Empty title="暂无可切换空间" description="可创建团队空间或加入已有空间" compact />;
  } else {
    listBody = (
      <div className="app-scrollbar max-h-[280px] overflow-y-auto p-1">
        {ordered.map((s) => (
          <SpaceRow
            key={s.spaceId || s.spaceCode}
            space={s}
            active={s.spaceId === currentSpaceId}
            onSelect={onSelect}
          />
        ))}
      </div>
    );
  }

  const currentType = currentSpace?.spaceType ?? 'TEAM';
  const displayText = currentSpace?.spaceName ?? (loading ? '加载中…' : '选择空间');

  return (
    <div className="space-y-1">
      <div className="flex items-center px-1 pb-1 text-xs font-semibold text-foreground">
        <span>管理空间</span>
      </div>
      <Popover open={open} onOpenChange={onOpenChange}>
        <PopoverTrigger asChild>
          <span className="inline-flex w-full">
            <Button
              variant="ghost"
              className={cn(
                'h-auto min-h-9 w-full justify-between gap-2 rounded-lg border border-border bg-muted/60 px-2.5 py-1.5 text-left text-foreground hover:bg-muted hover:text-foreground',
                open && 'border-primary',
              )}
            >
              <SpaceAvatar type={currentType} loading={loading} />
              <span className="min-w-0 flex-1 truncate text-left text-xs font-medium text-foreground">{displayText}</span>
              <ChevronDown
                className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
                aria-hidden
              />
            </Button>
          </span>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="bottom"
          sideOffset={8}
          className="w-[var(--radix-popper-anchor-width)] p-0"
        >
          <div className="px-3 pb-2 pt-2 text-xs font-semibold text-[var(--color-muted)]">空间切换</div>
          {listBody}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default SpaceSwitcher;
