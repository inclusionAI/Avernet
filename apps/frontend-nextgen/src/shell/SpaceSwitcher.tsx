// 空间切换器。新骨架（refactor-global-nav-shell）以 compact 形态挂在 Bot 分组标题右侧
//（展开=当前空间名 chip，折叠=icon+tooltip 含当前空间名）；默认形态保留原整块渲染供既有消费方。
// 读 useSpaceContext：当前空间展示 + Popover 下拉（仅已加入）。
// 选中即 switchSpaceContext(id) + 持久化。loading/error/empty 三态。
// 挂载即 initSpaceContext（读者挂载即拉取：切换器全局常驻、协作路由刷新也要首屏还原当前空间；
// 幂等+并发单飞，与 AppShell bot 路由 effect / 气泡打开的同帧触发共享同一次请求）。
// 打开气泡时再按节流窗 refreshSpaceContext 重拉，成员变更最迟一个窗口后可见。
// 视觉对齐 PRD（Teamclaw_PRD_new/src/components/Layout/Sidebar.tsx V1.7）：
//   - compact 触发器：图标+当前空间名（≤4em 截断）+下拉箭头；折叠态 Users icon + tooltip 带当前空间名
//   - 默认形态触发器：浅色填充圆角卡片 + border（展开时 border-primary）
//   - 下拉标题「空间切换」；列表项 图标+空间名，当前项 accent 高亮+primary 文字+右侧 CheckCircle 勾选
//   - 列表仅展示已加入空间，个人空间置顶，团队按 gmtModified 倒序
//   - 弹层标题行挂「空间管理」设置 icon → /space-admin（split-admin-space-ticket-pages：
//     管理域拆分后独立页入口；Open Core spaces=false 不渲染）
import { getCapabilities } from '@/capabilities';
import { Button, Empty, IconButton, Popover, PopoverContent, PopoverTrigger, Skeleton } from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { initSpaceContext, refreshSpaceContext, switchSpaceContext, useSpaceContext } from '@/hooks/useSpaceContext';
import { cn } from '@/utils/cn';
import { useNavigate } from '@umijs/max';
import { CheckCircle, ChevronDown, Loader2, Settings, User, Users } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

// 打开气泡的刷新节流窗口：窗口内复用 store 现有列表，避免反复开合连打 GET /spaces?page_size=100。
// plan.md D7「下次打开切换器时刷新」的兜底仍生效，远端变更最迟一个窗口后可见。
const REFRESH_THROTTLE_MS = 60_000;

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

export function SpaceSwitcher({ compact = false, collapsed = false }: { compact?: boolean; collapsed?: boolean } = {}) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const spacesManageable = getCapabilities().getAdminSections().value.spaces;
  const lastRefreshAt = useRef(0);
  const currentSpace = useSpaceContext((s) => s.currentSpace);
  const currentSpaceId = useSpaceContext((s) => s.currentSpaceId);
  const spaces = useSpaceContext((s) => s.spaces);
  const loading = useSpaceContext((s) => s.loading);
  const error = useSpaceContext((s) => s.error);

  // 挂载即初始化：切换器全局常驻侧栏（协作路由也可见），不能等进入 Bot 分组路由才拉取，
  // 否则协作路由刷新会停留「选择空间」。幂等+并发单飞：与 AppShell bot 路由 effect / 气泡打开
  // 的同帧触发共享同一次请求。
  useEffect(() => {
    void initSpaceContext();
  }, []);

  const onSelect = (id: number) => {
    switchSpaceContext(id);
    setOpen(false);
  };

  // 空间管理入口（split-admin-space-ticket-pages）：管理域拆分为 /space-admin 独立页后不再占导航位，
  // 设置 icon 挂弹层标题行；Open Core（getAdminSections.spaces=false）不渲染。
  const goSpaceAdmin = () => {
    setOpen(false);
    navigate('/space-admin');
  };
  const popoverHeader = (
    <div className="flex items-center justify-between px-3 pb-2 pt-2 text-xs font-semibold text-muted-foreground">
      <span>空间切换</span>
      {spacesManageable && (
        <IconButton size="sm" label="空间管理" icon={<Settings className="h-3.5 w-3.5" />} onClick={goSpaceAdmin} />
      )}
    </div>
  );

  // 打开气泡时先幂等初始化（入口交互触发空间上下文，refactor-global-nav-shell 3.1），
  // 再按 REFRESH_THROTTLE_MS 节流重拉（成员变更/新空间可能发生在上次初始化之后）
  const onOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) return;
    void initSpaceContext();
    const now = Date.now();
    if (now - lastRefreshAt.current < REFRESH_THROTTLE_MS) return;
    lastRefreshAt.current = now;
    void refreshSpaceContext();
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

  // compact：Bot 分组标题右侧小芯片；折叠态 icon-only + tooltip 含当前空间名
  if (compact) {
    return (
      <Popover open={open} onOpenChange={onOpenChange}>
        <PopoverTrigger asChild>
          <span className="inline-flex">
            {collapsed ? (
              <IconButton label={`切换空间（当前：${displayText}）`} icon={<Users className="h-4 w-4" />} />
            ) : (
              <Button
                variant="ghost"
                className="h-6 gap-1 rounded-md px-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Users className="h-3.5 w-3.5" aria-hidden />
                <span className="max-w-[4em] truncate">{displayText}</span>
                <ChevronDown
                  className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-180')}
                  aria-hidden
                />
              </Button>
            )}
          </span>
        </PopoverTrigger>
        <PopoverContent align="end" side="bottom" sideOffset={6} className="w-[180px] p-0">
          {popoverHeader}
          {listBody}
        </PopoverContent>
      </Popover>
    );
  }

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
              <span className="min-w-0 flex-1 truncate text-left text-xs font-medium text-foreground">
                {displayText}
              </span>
              <ChevronDown
                className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
                aria-hidden
              />
            </Button>
          </span>
        </PopoverTrigger>
        <PopoverContent align="start" side="bottom" sideOffset={8} className="w-[var(--radix-popper-anchor-width)] p-0">
          {popoverHeader}
          {listBody}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default SpaceSwitcher;
