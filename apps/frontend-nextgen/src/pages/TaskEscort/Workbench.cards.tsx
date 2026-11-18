import { useWorkbenchSummary } from '@/hooks/taskEscortWorkspace/useWorkbenchPresentation';
import { ArrowRight, Clock3, GitBranch, ListChecks, RotateCcw, ShieldAlert } from 'lucide-react';
import type { ViewProps } from './types';
import { Button, Panel, StatusBadge } from './ui';

export function WorkbenchPriorities({ demo }: ViewProps) {
  const { summary } = useWorkbenchSummary(demo);
  const priorities = [
    {
      label: '优先关注复发',
      count: summary.recurring,
      hint: '相同问题再次出现，建议先排查根因',
      status: 'recurring',
      icon: RotateCcw,
      accent: 'text-destructive',
      border: 'border-l-destructive/60',
    },
    {
      label: '等待你的处理',
      count: summary.open,
      hint: '已整理证据与建议，由你决定下一步',
      status: 'open',
      icon: ShieldAlert,
      accent: 'text-warning',
      border: 'border-l-warning/70',
    },
    {
      label: '建议已应用，待验证',
      count: summary.applied,
      hint: '手动确认修复效果后，再沉淀为经验',
      status: 'applied',
      icon: ListChecks,
      accent: 'text-primary',
      border: 'border-l-primary/70',
    },
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      {priorities.map(({ label, count, hint, status, icon: Icon, accent, border }) => (
        <section key={status} className={`rounded-xl border border-border border-l-[3px] bg-card px-5 py-4 ${border}`}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-medium">{label}</span>
            <Icon className={`size-4 ${accent}`} aria-hidden="true" />
          </div>
          <div className="mt-3 flex items-end justify-between gap-3">
            <span className="stat-value tabular-nums">
              {count}
              <span className="ml-2 text-xs font-normal text-muted-foreground">项</span>
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-0 text-xs text-primary hover:bg-transparent"
              aria-label={`筛选${label}`}
              onClick={() => {
                demo.setIssueFilter(status);
                demo.setQuery('');
                demo.setWorkflowId('all');
              }}
            >
              去查看
              <ArrowRight className="ml-1 size-3" />
            </Button>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{hint}</p>
        </section>
      ))}
    </div>
  );
}

export function WorkbenchWorkflows({ demo }: ViewProps) {
  return (
    <Panel
      title="关注中的工作流"
      description="运行健康度与待办放在一起，不遗漏关键任务"
      action={
        <Button
          variant="ghost"
          size="sm"
          className="text-xs text-primary"
          onClick={() => {
            demo.navigate('runs');
            demo.setQuery('');
            demo.setRunFilter('all');
            demo.setWorkflowId('all');
          }}
        >
          查看全部运行
          <ArrowRight className="ml-1 size-3.5" />
        </Button>
      }
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {demo.workflows.slice(0, 3).map((workflow) => (
          <div key={workflow.id} className="rounded-lg border border-border p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <GitBranch className="size-4 shrink-0 text-primary" />
                <h3 className="truncate text-sm font-medium">{workflow.name}</h3>
              </div>
              <StatusBadge status={workflow.status} />
            </div>
            <p className="mt-2 line-clamp-1 text-xs text-muted-foreground">{workflow.description}</p>
            <div className="mt-5 flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground">
                成功率{' '}
                <strong className="ml-1 font-medium text-foreground">
                  {workflow.successRate === null ? '—' : `${workflow.successRate}%`}
                </strong>
              </span>
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <Clock3 className="size-3" />
                {workflow.runCount} 次运行
              </span>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="mt-3 h-7 w-full justify-between px-0 text-xs text-primary hover:bg-transparent"
              aria-label={`查看${workflow.name}的运行`}
              onClick={() => {
                demo.navigate('runs');
                demo.setWorkflowId(workflow.id);
                demo.setQuery('');
                demo.setRunFilter('all');
              }}
            >
              查看运行记录
              <ArrowRight className="size-3" />
            </Button>
          </div>
        ))}
      </div>
    </Panel>
  );
}
