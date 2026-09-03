import { Button } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import { cn } from '@/utils/cn';

interface WorkspacePrimaryTabsProps {
  value: WorkspaceView;
  options: WorkspaceView[];
  onChange: (value: WorkspaceView) => void;
}

/** 工作台一级导航：所有视口统一使用轻量文字 Tab，避免与筛选项混成按钮组。 */
export function WorkspacePrimaryTabs({ value, options, onChange }: WorkspacePrimaryTabsProps) {
  return (
    <div
      className="flex h-9 min-w-0 flex-1 items-center gap-5 border-b border-border/70"
      role="tablist"
      aria-label="工作区类型"
    >
      {options.map((option) => {
        const active = value === option;
        return (
          <Button
            key={option}
            variant="ghost"
            size="sm"
            role="tab"
            aria-selected={active}
            aria-label={option === 'chat' ? '对话' : '协作群'}
            onClick={() => onChange(option)}
            className={cn(
              'h-9 rounded-none border-x-0 border-t-0 border-b-2 px-0 text-xs',
              active
                ? 'border-primary font-medium text-primary hover:bg-transparent hover:text-primary'
                : 'border-transparent font-normal text-muted-foreground hover:bg-transparent hover:text-foreground',
            )}
          >
            {option === 'chat' ? '对话' : '协作群'}
          </Button>
        );
      })}
    </div>
  );
}
