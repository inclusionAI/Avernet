import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { DEFAULT_MESSAGE_VIEW_SCOPE, MESSAGE_VIEW_SCOPE_OPTIONS } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { Check, Plus } from 'lucide-react';
import { useState } from 'react';

export interface MessageViewScopeMenuButtonProps {
  /** 点选项即以此 scope 执行动作（如创建会话）。 */
  onSelect: (scope: MessageViewScope) => void;
  /** 勾选态（默认 full）。 */
  selected?: MessageViewScope;
  disabled?: boolean;
}

/**
 * 「+」图标形态的新建会话入口：点击打开视角菜单，点选项即以该视角执行 onSelect。
 * 仅 human 身份使用；bot 身份直接创建会话，不经过本组件。
 */
export function MessageViewScopeMenuButton({ onSelect, selected, disabled }: MessageViewScopeMenuButtonProps) {
  const [open, setOpen] = useState(false);
  const current = selected ?? DEFAULT_MESSAGE_VIEW_SCOPE;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-4 w-4" />}
          disabled={disabled}
          onClick={(event) => event.stopPropagation()}
          className="rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-1">
        {MESSAGE_VIEW_SCOPE_OPTIONS.map((option) => (
          <Button
            key={option.value}
            variant="ghost"
            className={cn(
              'h-auto w-full items-start justify-start gap-2 px-2.5 py-2 text-left',
              current === option.value && 'border border-primary bg-primary/5',
            )}
            onClick={() => {
              setOpen(false);
              onSelect(option.value);
            }}
          >
            <Check
              className={cn('mt-0.5 h-4 w-4 shrink-0', current === option.value ? 'text-primary' : 'text-transparent')}
              aria-hidden="true"
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">{option.label}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>
            </span>
          </Button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
