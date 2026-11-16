// 通知铃铛：通知中心的快速预览入口。红点未读数 + Popover（最近3条 / 全部已读 / 查看全部）。
// 与通知中心完整视图（/ticket-center，split-admin-space-ticket-pages 拆分后的独立路由）同数据源：
// list 端点、未读 badge_count、WorkOrderDto 均共用。
// 视觉对齐通知中心设计：353px 弹层、53px 标题区、49px 底部入口。
import { Button, Empty, IconButton, Popover, PopoverContent, PopoverTrigger, Skeleton } from '@/components/ui';
import type { NotificationSummary, WorkOrderCategory } from '@/domain/admin/models';
import { useNotifications } from '@/hooks/useNotifications';
import { Points } from '@/tracking/points';
import { cn } from '@/utils/cn';
import { Tracert } from '@/utils/tracert';
import { useNavigate } from '@umijs/max';
import { Bell } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NotificationItem } from '../NotificationItem';

function categoryFromItemType(itemType: NotificationSummary['itemType']): WorkOrderCategory {
  return itemType === 'APPROVAL' ? 'APPROVAL' : itemType === 'NOTIFICATION' ? 'NOTIFICATION' : 'ALL';
}

export function NotificationBell({ size = 'md', className }: { size?: 'sm' | 'md'; className?: string }) {
  const navigate = useNavigate();
  const { unreadCount, recent, loadingRecent, loadRecent, markAllRead, markOneRead } = useNotifications();
  const [open, setOpen] = useState(false);
  // 全部已读 in-flight 态：按钮 loading 防抖，避免连点重复调用 markAllRead 接口。
  const [markingAll, setMarkingAll] = useState(false);
  const handleMarkAllRead = async () => {
    if (markingAll) return;
    setMarkingAll(true);
    try {
      await markAllRead();
    } finally {
      setMarkingAll(false);
    }
  };

  // 悬停/打开时懒拉最近 3 条（once-ish：每次打开都拉一次保证新鲜）。
  useEffect(() => {
    if (open) void loadRecent();
  }, [open, loadRecent]);

  const goWorkOrders = async (item?: NotificationSummary) => {
    // BBS notice carries a stable internal target. It navigates directly to the
    // topic and marker post; other items keep the notification-center category link.
    if (item?.bbsTarget?.topicId) {
      const { topicId, postId } = item.bbsTarget;
      const query = new URLSearchParams({ topicId });
      if (postId) query.set('postId', postId);
      if (!item.isRead) {
        void markOneRead(item.notificationId).finally(() => {
          void loadRecent();
        });
      }
      setOpen(false);
      navigate(`/lab/community?${query.toString()}`);
      return;
    }
    const cat = item ? categoryFromItemType(item.itemType) : null;
    setOpen(false);
    navigate(cat ? `/ticket-center?category=${cat}` : '/ticket-center');
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) Tracert.click(Points.shell.notifyOpen);
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <span className="relative inline-flex" data-guide-target="notification">
          <IconButton
            label="通知中心"
            // size/className 透传：折叠态工具簇经此收 28px + muted 色阶，bell-wiggle 等行为不受影响。
            size={size}
            className={className}
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
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-[353px] max-w-[calc(100vw-1rem)] overflow-hidden rounded-xl border-border/70 bg-background p-0 shadow-[0_4px_12px_rgba(0,0,0,0.16)]"
      >
        <div className="flex min-h-[53px] items-center justify-between border-b border-divider px-4">
          <p className="m-0 text-sm font-semibold text-foreground">最近通知</p>
          <Button
            variant="ghost"
            size="sm"
            className="h-auto p-0 text-xs font-normal text-muted-foreground hover:bg-transparent hover:text-foreground"
            disabled={unreadCount === 0 || markingAll}
            loading={markingAll}
            onClick={() => void handleMarkAllRead()}
          >
            全部已读
          </Button>
        </div>
        <div className="max-h-[min(360px,calc(100dvh-144px))] overflow-y-auto py-2">
          {/* 骨架对齐真实 NotificationItem(px-4,无容器外 padding,loading↔loaded 不左跳) */}
          {loadingRecent && recent.length === 0 ? (
            <div className="space-y-0.5">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-start gap-2 px-4 py-3">
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
        <div className="flex min-h-[49px] items-center justify-center border-t border-divider px-4">
          <Button
            variant="ghost"
            size="sm"
            className="h-auto w-full py-3 text-xs font-semibold hover:bg-transparent hover:text-primary"
            onClick={() => void goWorkOrders()}
          >
            查看全部
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
