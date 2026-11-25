import aiBadgeIcon from '@/assets/Images/collaborationSquare/AI.png';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotSearchMode, PublicBotSearchResultMode } from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Search, X } from 'lucide-react';

/** 智能搜索模式标记（dmore）：「智能」紫蓝渐变**文字色** + AI 图标，无胶囊背景。
 * 渐变文字走内联 style（background-clip: text + 透明字），不受 Tailwind 类生成/缓存干扰。 */
function SmartModePill() {
  return (
    <span
      style={{
        background: 'linear-gradient(225.32deg, #AC56FF -2.66%, #006AFF 108.32%)',
        backgroundClip: 'text',
        WebkitBackgroundClip: 'text',
        color: 'transparent',
        WebkitTextFillColor: 'transparent',
      }}
      className="inline-flex shrink-0 items-center gap-0.5 font-normal"
    >
      智能
      <img src={aiBadgeIcon} alt="" aria-hidden className="h-3.5 w-3.5" />
    </span>
  );
}

/** 智能搜索降级为名称匹配时的回退标记（bcs-standalone-friend-discovery）：普通前景色文字，
 * 明确告知用户当前批次是名称回退命中而非语义命中，不叠渐变误导。 */
function NameFallbackPill() {
  return <span className="inline-flex shrink-0 items-center font-normal text-muted-foreground">名称匹配</span>;
}

interface SquareSearchBarProps {
  resource: 'bot' | 'group' | 'task';
  query: string;
  mode?: BotSearchMode;
  /** 智能搜索的 wire 结果模式：name_fallback 时触发器以「名称匹配」替代「智能」渐变胶囊。 */
  searchResultMode?: PublicBotSearchResultMode | null;
  onQueryChange: (query: string) => void;
  onModeChange?: (mode: BotSearchMode) => void;
  className?: string;
}

/** 宽度与容器样式（dmore index.html Frame 2）：页头内 32px 高、8px 圆角、描边输入组。 */
function framedClassName(hasFrame: boolean) {
  return cn(
    'relative flex w-full items-center',
    hasFrame ? 'h-8 rounded-lg border border-input bg-background pr-1' : 'h-8',
  );
}

export default function SquareSearchBar({
  resource,
  query,
  mode = 'name',
  searchResultMode,
  onQueryChange,
  onModeChange,
  className,
}: SquareSearchBarProps) {
  const label =
    resource === 'bot'
      ? mode === 'smart'
        ? '描述你需要的职责或能力'
        : '输入Bot名称'
      : resource === 'group'
      ? '搜索协作群名称'
      : '搜索任务';
  const handleModeChange = (nextMode: BotSearchMode) => {
    if (nextMode !== mode) onQueryChange('');
    onModeChange?.(nextMode);
  };
  const hasModeSelect = resource === 'bot' && Boolean(onModeChange);
  const framed = hasModeSelect || resource === 'group';
  return (
    <div className={cn('flex min-w-0 flex-col', className)}>
      <div className={framedClassName(framed)}>
        {hasModeSelect && (
          <div className="relative flex h-full shrink-0 items-center border-r border-divider/80">
            <Select value={mode} onValueChange={(value) => handleModeChange(value as BotSearchMode)}>
              <SelectTrigger
                aria-label="Bot 搜索模式"
                className="h-full w-auto gap-1.5 border-0 bg-transparent px-2.5 font-normal text-foreground hover:bg-accent focus-visible:ring-0 focus-visible:ring-offset-0"
              >
                {/* name_fallback 时以「名称匹配」回退标记替代渐变胶囊，明示非语义命中。 */}
                {mode === 'smart' ? (
                  searchResultMode === 'name_fallback' ? (
                    <NameFallbackPill />
                  ) : (
                    <SmartModePill />
                  )
                ) : (
                  <SelectValue />
                )}
              </SelectTrigger>
              <SelectContent align="start" className="min-w-28 rounded-lg p-1 shadow-lg">
                <SelectItem value="name" className="min-h-9 font-normal text-muted-foreground focus:text-muted-foreground data-[state=checked]:bg-accent data-[state=checked]:text-muted-foreground [&>span:first-child]:text-primary">
                  按名称
                </SelectItem>
                <SelectItem value="smart" className="min-h-9 font-normal text-muted-foreground focus:text-muted-foreground data-[state=checked]:bg-accent data-[state=checked]:text-muted-foreground [&>span:first-child]:text-primary">
                  {/* 下拉内同样使用渐变文字标记；内联透明字不受选中态 text-* 类影响。 */}
                  <SmartModePill />
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        {/* 搜索图标按钮（dmore：32px 方形、10px 圆角、18px 图标）。 */}
        <Search
          aria-hidden
          className={cn(
            'h-4.5 w-4.5 text-muted-foreground',
            framed ? 'ml-2.5 shrink-0' : 'absolute left-3 top-1/2 -translate-y-1/2',
          )}
        />
        <Input
          aria-label={label}
          value={query}
          placeholder={label}
          onChange={(event) => onQueryChange(event.target.value)}
          className={cn('h-8 min-w-0 pr-10', framed
            ? 'flex-1 border-0 bg-transparent pl-2 shadow-none focus-visible:border-transparent focus-visible:ring-0 focus-visible:ring-offset-0'
            : 'pl-9')}
        />
        {query && (
          <IconButton
            label="清除搜索"
            icon={<X aria-hidden className="h-4 w-4" />}
            onClick={() => onQueryChange('')}
            className="absolute right-0 top-0"
          />
        )}
      </div>
    </div>
  );
}
