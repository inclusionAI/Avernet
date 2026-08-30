// 通知下拉单条。未读浅蓝底 + 未读 dot；已读透明无 dot。点击跳工单中心对应分类。
import { Button } from '@/components/ui';
import type { NotificationSummary } from '@/domain/admin/models';
import { cn } from '@/utils/cn';
import { formatRelativeTime } from '@/utils/format';

export interface NotificationItemProps {
  item: NotificationSummary;
  onClick?: (item: NotificationSummary) => void;
}

export function NotificationItem({ item, onClick }: NotificationItemProps) {
  const unread = !item.isRead;
  return (
    <Button
      variant="ghost"
      className={cn(
        'h-auto w-full justify-start rounded-lg px-4 py-2.5 text-left',
        unread && 'bg-[var(--color-primary)]/5',
      )}
      onClick={() => onClick?.(item)}
    >
      <div className="flex w-full items-start gap-2">
        <span className="mt-1 h-1.5 w-1.5 shrink-0">
          {unread && <span className="block h-full w-full rounded-full bg-[var(--color-primary)]" aria-label="未读" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="m-0 truncate text-sm font-medium text-foreground">{item.title}</p>
          {item.content && (
            <p className="m-0 mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.content}</p>
          )}
          <p className="m-0 mt-1 text-[10px] text-muted-foreground">{formatRelativeTime(item.gmtModified)}</p>
        </div>
      </div>
    </Button>
  );
}

export default NotificationItem;
