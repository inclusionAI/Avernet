// 通知铃铛：红点未读数 + Popover（最近3条 / 全部已读 / 查看全部通知）。
// 视觉规格：docs/specs/2026-08-17-admin-module/prd-visual-spec.md §4（Popover 约 360 宽）。
import { Button, Empty, IconButton, Popover, PopoverContent, PopoverTrigger, Skeleton } from '@/components/ui';
import type { NotificationSummary, WorkOrderCategory } from '@/domain/admin/models';
import { useNotifications } from '@/hooks/useNotifications';
import { cn } from '@/utils/cn';
import { useNavigate } from '@umijs/max';
import { Bell } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NotificationItem } from '../NotificationItem';

function categoryFromItemType(itemType: NotificationSummary['itemType']): WorkOrderCategory {
  return itemType === 'APPROVAL' ? 'APPROVAL' : itemType === 'NOTIFICATION' ? 'NOTIFICATION' : 'ALL';
}

export function NotificationBell() {
  const navigate = useNavigate();
  const { unreadCount, recent, loadingRecent, loadRecent, markAllRead } = useNotifications();
  const [open, setOpen] = useState(false);

  // 悬停/打开时懒拉最近 3 条（once-ish：每次打开都拉一次保证新鲜）。
  useEffect(() => {
    if (open) void loadRecent();
  }, [open, loadRecent]);

  const goWorkOrders = (item?: NotificationSummary) => {
    const cat = item ? categoryFromItemType(item.itemType) : 'ALL';
    navigate(`/admin?tab=work-orders&category=${cat}`);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="relative inline-flex">
          <IconButton
            label="通知中心"
            icon={<Bell className={cn('h-4 w-4', unreadCount > 0 && 'bell-wiggle')} aria-hidden />}
          />
          {unreadCount > 0 && (
            <span
              className={cn(
                'pointer-events-none absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center',
                'rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground',
              )}
              aria-label={`${unreadCount} 条未读`}
            >
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </span>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="w-[360px] max-w-[calc(100vw-1rem)] p-0">
        <div className="flex items-center justify-between px-4 pt-3 pb-2">
          <p className="m-0 text-sm font-semibold text-foreground">通知中心</p>
          {unreadCount > 0 && (
            <Button variant="ghost" size="sm" onClick={() => void markAllRead()}>
              全部已读
            </Button>
          )}
        </div>
        <div className="max-h-[440px] overflow-y-auto">
          {/* 骨架对齐真实 NotificationItem(px-4,无容器外 padding,loading↔loaded 不左跳) */}
          {loadingRecent && recent.length === 0 ? (
            <div className="space-y-0.5">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-start gap-2 px-4 py-2.5">
                  <Skeleton.Block className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton.Line className="w-3/4" />
                    <Skeleton.Line className="w-full" />
                    <Skeleton.Line className="w-1/4" />
                  </div>
                </div>
              ))}
            </div>
          ) : recent.length === 0 ? (
            <Empty title="暂无通知" compact description="当前没有未读消息" />
          ) : (
            <div className="space-y-0.5">
              {recent.map((item) => (
                <NotificationItem key={item.itemId || item.notificationId} item={item} onClick={goWorkOrders} />
              ))}
            </div>
          )}
        </div>
        <div className="border-t border-border px-4 py-2">
          <Button variant="ghost" size="sm" className="w-full" onClick={() => goWorkOrders()}>
            查看全部通知
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
