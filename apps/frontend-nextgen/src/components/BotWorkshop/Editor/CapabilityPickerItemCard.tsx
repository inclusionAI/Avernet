import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { Check, ExternalLink, Plug, Shapes } from 'lucide-react';

type PickerItem = BotEditorSkill | BotEditorMcp;

export function pickerItemId(item: PickerItem): string {
  return 'serverCode' in item ? item.serverCode : item.id;
}

interface CapabilityPickerItemCardProps {
  item: PickerItem;
  kind: 'skill' | 'mcp';
  active: boolean;
  alreadyAdded: boolean;
  selectionDisabled: boolean;
  onSelect: () => void;
  onViewSkill?: (skill: BotEditorSkill) => void;
}

export function CapabilityPickerItemCard({
  item,
  kind,
  active,
  alreadyAdded,
  selectionDisabled,
  onSelect,
  onViewSkill,
}: CapabilityPickerItemCardProps) {
  return (
    <div
      className={`flex min-h-24 min-w-0 flex-col rounded-md border border-border p-3 text-left ${
        active ? 'border-primary bg-accent' : ''
      } ${alreadyAdded ? 'opacity-50' : ''}`}
    >
      <Button
        variant="ghost"
        className="h-auto min-h-0 w-full flex-1 items-start justify-start whitespace-normal border-0 p-0 text-left hover:bg-transparent"
        disabled={selectionDisabled}
        onClick={onSelect}
      >
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted">
          {kind === 'skill' ? <Shapes className="size-4" /> : <Plug className="size-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-xs font-medium">{item.name}</span>
            {'version' in item && item.version ? <Badge>{item.version}</Badge> : null}
            {alreadyAdded ? <Badge tone="primary">已添加</Badge> : null}
          </span>
          <span className="mt-1 line-clamp-2 text-xs font-normal text-muted-foreground">
            {item.description || '暂无描述'}
          </span>
        </span>
        {active ? <Check className="size-4 shrink-0 text-primary" /> : null}
      </Button>
      {kind === 'skill' ? (
        <Button
          variant="link"
          className="ml-9 h-auto w-fit justify-start px-0 py-0 text-xs"
          aria-label={`查看${item.name} Skill 详情`}
          disabled={alreadyAdded || !onViewSkill}
          onClick={() => onViewSkill?.(item as BotEditorSkill)}
        >
          <ExternalLink className="size-3" aria-hidden /> 查看 Skill 详情
        </Button>
      ) : null}
    </div>
  );
}
