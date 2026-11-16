import {
  Badge,
  Button,
  Checkbox,
  IconButton,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui';
import type { CodingBotSessionArchiveItem } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { Eye, MoreHorizontal, RotateCcw } from 'lucide-react';
import { useState } from 'react';
import { archiveTitle, rowMeta } from './ConversationSessionArchivePresentation';

function ArchiveRowActions({
  item,
  canRestore,
  restoring,
  submitting,
  onPreview,
  onRestore,
}: {
  item: CodingBotSessionArchiveItem;
  canRestore: boolean;
  restoring: boolean;
  submitting: boolean;
  onPreview(item: CodingBotSessionArchiveItem): void;
  onRestore(item: CodingBotSessionArchiveItem): void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const handleAction = (action: () => void) => {
    setMenuOpen(false);
    action();
  };
  return (
    <Popover open={menuOpen} onOpenChange={setMenuOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label="归档会话操作"
          size="sm"
          icon={<MoreHorizontal className="h-3.5 w-3.5" />}
          className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1">
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
          onClick={() => handleAction(() => onPreview(item))}
        >
          <Eye className="h-3.5 w-3.5" />
          查看消息摘要
        </Button>
        {canRestore && (
          <Button
            variant="ghost"
            className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
            disabled={restoring || submitting}
            loading={submitting}
            onClick={() => handleAction(() => onRestore(item))}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {item.restoreStatus === 'failed' ? '重试恢复' : '恢复会话'}
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}

export interface ConversationSessionArchiveRowProps {
  item: CodingBotSessionArchiveItem;
  selectionMode: boolean;
  restoreSupported: boolean;
  checked: boolean;
  selectedCount: number;
  selectionLimit: number;
  submitting: boolean;
  onCheckedChange(checked: boolean): void;
  onPreview(item: CodingBotSessionArchiveItem): void;
  onRestore(item: CodingBotSessionArchiveItem): void;
}

export function ConversationSessionArchiveRow({
  item,
  selectionMode,
  restoreSupported,
  checked,
  selectedCount,
  selectionLimit,
  submitting,
  onCheckedChange,
  onPreview,
  onRestore,
}: ConversationSessionArchiveRowProps) {
  const title = archiveTitle(item);
  const canRestore =
    restoreSupported && (item.restoreStatus === 'archived' || item.restoreStatus === 'failed');
  const restoring = item.restoreStatus === 'queued' || item.restoreStatus === 'restoring';
  const selected = selectionMode && checked;
  const titleActionPadding = selectionMode
    ? []
    : [
        'group-hover/row:pr-[34px]',
        'group-focus-within/row:pr-[34px]',
        'group-has-[[data-state=open]]/row:pr-[34px]',
        '[@media(hover:none)]:pr-[34px]',
      ];

  return (
    <div className="group/row relative flex min-h-8 items-center">
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-2 right-2 rounded-md',
          selected || item.restoreStatus === 'succeeded'
            ? 'bg-foreground/[6%]'
            : 'bg-muted/60 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
        )}
      />
      {selectionMode && restoreSupported && (
        <span
          // Reuse the exact leading slot geometry of “Bot 正在回复”:
          // 14px square at x=40. The title text then starts at x=60,
          // keeping the checkbox immediately left of the 13px title.
          // Use an inline value because an arbitrary Tailwind class is not a
          // semantic contract and can miss one-off dev CSS generation.
          style={{ left: '40px' }}
          className="absolute top-1/2 z-10 flex size-3.5 -translate-y-1/2 items-center justify-center"
        >
          <Checkbox
            className="size-3.5 rounded-[4px]"
            aria-label={`选择 ${title}`}
            checked={checked}
            disabled={
              !canRestore || restoring || submitting || (selectedCount >= selectionLimit && !checked)
            }
            onCheckedChange={(value) => onCheckedChange(Boolean(value))}
          />
        </span>
      )}
      <div
        className={cn(
          // 与普通会话行同款标题起点；Archive 行仅承担展示，不加自选中热区。
          'relative z-10 ml-[52px] mr-2 flex h-8 min-w-0 flex-1 items-center gap-1 rounded-md pl-2 pr-2',
          'transition-[padding] duration-150',
          titleActionPadding,
        )}
      >
        <p
          className={cn(
            'min-w-0 flex-1 truncate text-[13px] font-normal leading-[18px] text-content-strong',
            selected && 'text-foreground',
          )}
        >
          {title}
        </p>
        {item.sessionKind === 'diagnosis' && (
          <Badge tone="warning" className="shrink-0 rounded px-1 text-[10px]">
            诊断
          </Badge>
        )}
        {item.restoreStatus === 'succeeded' && (
          <Badge tone="primary" className="shrink-0 rounded px-1 text-[10px]">
            刚恢复
          </Badge>
        )}
        <span
          aria-hidden="true"
          className="ml-1 shrink-0 text-[11px] font-normal leading-none text-content-hint group-hover/row:opacity-0 group-focus-within/row:opacity-0 group-has-[[data-state=open]]/row:opacity-0 [@media(hover:none)]:opacity-0"
        >
          {rowMeta(item)}
        </span>
      </div>
      {!selectionMode && (
        <span
          className={cn(
            'absolute right-3 top-1/2 z-10 flex -translate-y-1/2 items-center',
            'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 group-has-[[data-state=open]]/row:opacity-100 [@media(hover:none)]:opacity-100',
            submitting && 'pointer-events-none',
          )}
        >
          <ArchiveRowActions
            item={item}
            canRestore={canRestore}
            restoring={restoring}
            submitting={submitting}
            onPreview={onPreview}
            onRestore={onRestore}
          />
        </span>
      )}
    </div>
  );
}
