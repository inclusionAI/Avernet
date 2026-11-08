import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { Check, ExternalLink, Loader2, Plug, Shapes, Trash2 } from 'lucide-react';

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
  /** 「我的 Skill」未添加项悬停显示的删除按钮（DELETE /openapi/v1/bots/{bot_id}/skills/{skill_id}，由上层回调实现）。 */
  onDelete?: () => void;
  deleting?: boolean;
  onSelect: () => void;
  onViewSkill?: (skill: BotEditorSkill) => void;
}

export function CapabilityPickerItemCard({
  item,
  kind,
  active,
  alreadyAdded,
  selectionDisabled,
  onDelete,
  deleting = false,
  onSelect,
  onViewSkill,
}: CapabilityPickerItemCardProps) {
  return (
    <div
      className={`group relative flex min-h-24 min-w-0 flex-col rounded-md border border-border p-3 text-left ${
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
            {/* 与 /market/skill MarketItemCard 徽标语义对齐：官方紫、推荐橙；SkillCenter 来源才会回传这两个 additive 字段 */}
            {'isOfficial' in item && item.isOfficial ? <Badge tone="purple">官方</Badge> : null}
            {'isRecommended' in item && item.isRecommended ? <Badge tone="warning">推荐</Badge> : null}
            {'version' in item && item.version ? <Badge>{item.version}</Badge> : null}
            {alreadyAdded ? <Badge tone="primary">已添加</Badge> : null}
          </span>
          <span className="mt-1 line-clamp-2 text-xs font-normal text-muted-foreground">
            {item.description || '暂无描述'}
          </span>
        </span>
        {active ? <Check className="size-4 shrink-0 text-primary" /> : null}
      </Button>
      {'tags' in item && Array.isArray(item.tags) && item.tags.length > 0 ? (
        <div className="ml-9 mt-1 flex min-w-0 items-center gap-1">
          {/* SC tagList 拼为单 chip（先用 / 分隔），与 MarketItemCard SkillCardFooterRow 一致；无内容不渲染 */}
          <Badge tone="neutral" className="min-w-0 truncate">
            {item.tags.join(' / ')}
          </Badge>
        </div>
      ) : null}
      {onDelete && !alreadyAdded ? (
        <Button
          variant="ghost"
          size="icon"
          className="absolute right-1.5 top-1.5 z-10 size-6 p-0 text-destructive opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          aria-label={`删除${item.name}`}
          disabled={deleting}
          leftIcon={deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
          onClick={() => onDelete()}
        />
      ) : null}
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
