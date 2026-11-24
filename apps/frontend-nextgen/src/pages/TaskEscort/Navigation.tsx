import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { PageId } from '@/domain/taskEscortWorkspace/types';
import { cn } from '@/utils/cn';
import { Activity, CircleHelp, Code2, Layers3, LayoutDashboard, SlidersHorizontal } from 'lucide-react';

export const pages: {
  id: Exclude<PageId, 'workbench'>;
  label: string;
  shortLabel: string;
  title: string;
  description: string;
  icon: typeof Activity;
  group: 'main' | 'more';
}[] = [
  {
    id: 'dashboard',
    label: '数据大盘',
    shortLabel: '大盘',
    title: '数据大盘',
    description: '查看工作流运行指标、趋势与记录。',
    icon: LayoutDashboard,
    group: 'main',
  },
  {
    id: 'runs',
    label: '运行概览',
    shortLabel: '运行',
    title: '运行概览',
    description: '查看当前工作流的运行状态与近期表现',
    icon: Activity,
    group: 'more',
  },
  {
    id: 'issues',
    label: '问题与优化',
    shortLabel: '问题',
    title: '问题与优化',
    description: '从异常证据到建议、应用与效果验证',
    icon: CircleHelp,
    group: 'more',
  },
  {
    id: 'experiences',
    label: '可复用经验',
    shortLabel: '经验',
    title: '可复用经验',
    description: '查看经过边界审核、可复用的知识与修法',
    icon: Layers3,
    group: 'more',
  },
  {
    id: 'editor',
    label: '编辑器',
    shortLabel: '编辑',
    title: '编辑器',
    description: '编辑工作流结构、节点与执行配置',
    icon: Code2,
    group: 'more',
  },
  {
    id: 'settings',
    label: '管理设置',
    shortLabel: '管理',
    title: '管理设置',
    description: '管理版本、权限、通知与回调配置',
    icon: SlidersHorizontal,
    group: 'more',
  },
];

const mainTabs = pages.filter((p) => p.group === 'main');
const moreTabs = pages.filter((p) => p.group === 'more');

export function TaskEscortNavigation({ page, navigate }: { page: PageId; navigate: (page: PageId) => void }) {
  const current = page === 'workbench' ? 'issues' : page;

  return (
    <TooltipProvider delayDuration={0}>
      <nav
        aria-label="任务护航导航"
        className="app-scrollbar flex w-12 shrink-0 flex-col items-center gap-3 overflow-y-auto border-r border-divider bg-card py-3"
      >
        {mainTabs.map((item) => (
          <Tooltip key={item.id}>
            <TooltipTrigger asChild>
              <Button
                aria-label={item.label}
                aria-current={current === item.id ? 'page' : undefined}
                variant="ghost"
                size="icon"
                className={cn(
                  'size-11 flex-col gap-1 rounded-lg text-[13px] font-normal text-muted-foreground',
                  current === item.id && 'bg-muted text-foreground hover:bg-muted hover:text-foreground',
                )}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={20} aria-hidden />
                <span className="leading-4">{item.shortLabel}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              <p className="font-medium">{item.label}</p>
              <p className="text-xs text-muted-foreground">{item.description}</p>
            </TooltipContent>
          </Tooltip>
        ))}

        {mainTabs.length > 0 && moreTabs.length > 0 && (
          <span aria-hidden className="h-px w-6 bg-divider" />
        )}

        {moreTabs.map((item) => (
          <Tooltip key={item.id}>
            <TooltipTrigger asChild>
              <Button
                aria-label={item.label}
                aria-current={current === item.id ? 'page' : undefined}
                variant="ghost"
                size="icon"
                className={cn(
                  'size-11 flex-col gap-1 rounded-lg text-[13px] font-normal text-muted-foreground',
                  current === item.id && 'bg-muted text-foreground hover:bg-muted hover:text-foreground',
                )}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={20} aria-hidden />
                <span className="leading-4">{item.shortLabel}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              <p className="font-medium">{item.label}</p>
              <p className="text-xs text-muted-foreground">{item.description}</p>
            </TooltipContent>
          </Tooltip>
        ))}
      </nav>
    </TooltipProvider>
  );
}
