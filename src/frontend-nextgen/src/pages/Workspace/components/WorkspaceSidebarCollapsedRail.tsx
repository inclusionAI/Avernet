import { IconButton } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import { cn } from '@/utils/cn';
import { MessageSquare, UsersRound } from 'lucide-react';

interface WorkspaceSidebarCollapsedRailProps {
  value: WorkspaceView;
  options: WorkspaceView[];
  onChange: (value: WorkspaceView) => void;
}

const VIEW_META = {
  chat: {
    label: '切换到对话',
    icon: MessageSquare,
  },
  group: {
    label: '切换到协作群',
    icon: UsersRound,
  },
} satisfies Record<WorkspaceView, { label: string; icon: typeof MessageSquare }>;

/** 桌面对话协作左栏收起态快捷入口；只保留当前身份可用视图。 */
export function WorkspaceSidebarCollapsedRail({ value, options, onChange }: WorkspaceSidebarCollapsedRailProps) {
  return (
    <nav aria-label="对话协作快捷切换" className="flex w-full flex-col items-center gap-2 py-3">
      {options.map((option) => {
        const meta = VIEW_META[option];
        const Icon = meta.icon;
        const active = option === value;
        return (
          <IconButton
            key={option}
            label={meta.label}
            icon={<Icon className="h-4 w-4" aria-hidden="true" />}
            aria-pressed={active}
            onClick={() => onChange(option)}
            className={cn(
              'h-9 w-9 rounded-md text-muted-foreground hover:bg-primary/5 hover:text-primary',
              active && 'bg-primary/10 text-primary',
            )}
          />
        );
      })}
    </nav>
  );
}
