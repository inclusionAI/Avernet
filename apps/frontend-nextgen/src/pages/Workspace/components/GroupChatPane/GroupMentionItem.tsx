import { Badge } from '@/components/ui/Badge';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { cn } from '@/utils/cn';
import type { MentionItem } from '@tc-chat/ui';

/** 自定义行内容；点击、键盘选中与高亮仍由 SDK 按 item.disabled 处理。 */
export function renderGroupMentionItem(item: MentionItem) {
  const { avatar, data, description, disabled, id, name } = item;
  const isBot = data?.kind === 'bot';
  const role = isBot ? data.role : undefined;
  const label = role === 'manager' ? '主节点' : role === 'worker' ? '从节点' :
    disabled && data?.kind === 'human' ? '勿扰' : null;
  return (
    <div aria-disabled={disabled || undefined} className="flex min-w-0 flex-1 items-center gap-2.5">
      <span
        className={cn(
          'flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-full text-xs font-semibold',
          disabled ? 'bg-muted text-muted-foreground grayscale opacity-60' : 'bg-primary/10 text-primary',
        )}
      >
        {isBot ? (
          <BotDisplayAvatar botId={id} name={name} avatarUrl={typeof avatar === 'string' ? avatar : undefined} size={28} />
        ) : typeof avatar === 'string' && avatar ? (
          <img src={avatar} alt={name} className="size-full object-cover" />
        ) : (
          avatar || name.slice(0, 1).toUpperCase()
        )}
      </span>
      <span className="flex min-w-0 flex-col gap-px">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className={cn('truncate text-sm font-medium', disabled ? 'text-muted-foreground' : 'text-foreground')}>
            {name}
          </span>
          {label ? (
            <Badge tone={role === 'manager' ? 'purple' : 'neutral'} className="shrink-0">{label}</Badge>
          ) : null}
        </span>
        {description ? <span className="truncate text-[11px] text-muted-foreground">{description}</span> : null}
      </span>
    </div>
  );
}
