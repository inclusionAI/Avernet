// 通知预览：白底、红色未读点，标题与时间同行；沿用当前用户的本地时间显示。
import { Button } from '@/components/ui';
import type { NotificationSummary } from '@/domain/admin/models';
import { formatNotificationTime } from '@/utils/notificationTime';

export interface NotificationItemProps {
  item: NotificationSummary;
  onClick?: (item: NotificationSummary) => void;
}

export function NotificationItem({ item, onClick }: NotificationItemProps) {
  return (
    <Button
      variant="ghost"
      className="group h-auto w-full justify-start whitespace-normal rounded-none px-4 py-2.5 text-left hover:bg-transparent focus-visible:bg-primary/5"
      onClick={() => onClick?.(item)}
    >
      <div className="flex w-full items-start gap-2">
        <span className="mt-[7px] h-1.5 w-1.5 shrink-0">
          {!item.isRead && <span className="block h-full w-full rounded-full bg-destructive" aria-label="未读" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
            <p className="m-0 min-w-0 flex-1 break-words text-[13px] font-medium leading-[18px] text-foreground group-hover:text-primary group-focus-visible:text-primary">
              {item.title}
            </p>
            {item.gmtModified && (
              <time
                dateTime={item.gmtModified}
                className="shrink-0 text-xs font-normal leading-[18px] text-muted-foreground"
              >
                {formatNotificationTime(item.gmtModified)}
              </time>
            )}
          </div>
          {item.content && (
            <p className="m-0 mt-1 line-clamp-2 whitespace-pre-wrap break-words text-xs font-normal leading-[17px] text-muted-foreground">
              {item.content}
            </p>
          )}
        </div>
      </div>
    </Button>
  );
}

export default NotificationItem;
