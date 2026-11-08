// 🔍 会话ID检索入口：命中标记（marked）以品牌蓝点亮图标并悬停右上圆点，
// 与 Bot 工坊同语言；未命中保持 muted 中性态。点击转发外部 onToggle（含展开公告逻辑）。
import { IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Search } from 'lucide-react';

export function ConversationSessionSearchIconButton({
  marked,
  onToggle,
}: {
  /** 已生效的检索标记：激活态钉住品牌蓝（Bot 收起时仍可见的 🔍 圆点）。 */
  marked: boolean;
  onToggle: () => void;
}) {
  return (
    <IconButton
      label="会话ID检索"
      size="sm"
      icon={
        <span className="relative flex items-center justify-center">
          <Search className="h-3.5 w-3.5" aria-hidden="true" />
          {marked && (
            <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
          )}
        </span>
      }
      className={cn(
        'h-6 w-6 rounded-md hover:bg-muted',
        marked ? 'text-primary hover:text-primary' : 'text-muted-foreground hover:text-foreground',
      )}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    />
  );
}