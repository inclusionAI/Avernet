import { Badge, Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { CollaborationBotView, RecommendCandidateView } from '@/services/workspace/collaborationCandidateService';
import { cn } from '@/utils/cn';
import { Check, Plus, Sparkles } from 'lucide-react';

/** 禁用成员 Bot 的整行悬停提示：统一列举不可选原因。 */
const UNAVAILABLE_BOT_TOOLTIP =
  '该 Bot 暂时无法参与新协作，可能原因为：已注销或删除、处于隐身状态、当前不在线';

function avatarText(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || 'B';
}

interface ParticipantRowProps {
  bot: CollaborationBotView;
  selected: boolean;
  /** 推荐态：展示画像摘要，截断时 hover Tooltip 全量展示。 */
  recommendActive: boolean;
  /** 是否发起方置顶 Bot。 */
  isOriginator: boolean;
  cardMode: boolean;
  onToggle: (id: string) => void;
}

/** 成员 Bot 列表行：勾选态、在线状态、画像摘要与禁用态悬停解释。 */
export function ParticipantRow({ bot, selected, recommendActive, isOriginator, cardMode, onToggle }: ParticipantRowProps) {
  const unknown = bot.detailsResolved === false;
  const unavailable = unknown || bot.reachability === 'unreachable' || !bot.online;
  const shortProfile = (bot as Partial<RecommendCandidateView>).shortProfile;
  const row = (
    <Button
      type="button"
      variant="ghost"
      size="md"
      disabled={unavailable}
      aria-pressed={selected}
      className={cn(
        'h-auto min-w-0 w-full max-w-full justify-start gap-2 rounded-none border-0 px-3 py-2 text-left',
        unknown
          ? 'cursor-not-allowed bg-muted/40 text-muted-foreground'
          : cardMode
          ? selected
            ? 'rounded-lg border border-primary bg-primary/10 hover:bg-primary/10'
            : 'rounded-lg border border-border bg-card hover:border-primary/30 hover:bg-primary/10'
          : selected
          ? 'bg-primary/10 hover:bg-primary/10'
          : 'hover:bg-muted/50',
        unknown && cardMode && 'rounded-lg border border-border',
      )}
      onClick={() => onToggle(bot.id)}
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
        {avatarText(bot.name)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-semibold text-foreground">{bot.name}</span>
          {isOriginator && (
            <span className="shrink-0 rounded-full border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
              发起方
            </span>
          )}
          <Badge tone={unavailable ? 'neutral' : 'primary'} className="flex shrink-0 items-center gap-1">
            <i className={cn('h-1.5 w-1.5 rounded-full', bot.online ? 'bg-success' : 'bg-muted')} />
            {unknown ? '未知' : bot.online ? '在线' : '隐身'}
          </Badge>
        </span>
        {bot.summary && <span className="mt-1 block truncate text-xs text-muted-foreground">{bot.summary}</span>}
        {recommendActive && shortProfile && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="mt-0.5 flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                  <Sparkles className="h-3 w-3 shrink-0 text-amber-400" aria-hidden />
                  <span className="truncate">{shortProfile}</span>
                </span>
              </TooltipTrigger>
              <TooltipContent>{shortProfile}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </span>
      <span
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border',
          selected ? 'border-primary bg-primary' : 'border-primary/20 bg-card',
        )}
      >
        {selected ? <Check className="h-4 w-4 text-primary-foreground" aria-hidden /> : <Plus className="h-4 w-4 text-primary" />}
      </span>
    </Button>
  );
  if (!unavailable) return row;
  // 禁用 Button 不派发鼠标事件，外包 span 使整行可悬停，解释不可选原因。
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="block w-full">{row}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-[280px] whitespace-normal">{UNAVAILABLE_BOT_TOOLTIP}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export default ParticipantRow;