import { useWorkbenchSummary } from '@/hooks/taskEscortWorkspace/useWorkbenchPresentation';
import { ArrowRight, Check, CircleAlert, GitBranch, Sparkles } from 'lucide-react';
import type { PageId, ViewProps } from './types';
import { Badge, Button, Panel } from './ui';

interface DashboardIssueCounts {
  recurring: number;
  applied: number;
  open: number;
}

interface DashboardProgressProps extends ViewProps {
  counts: DashboardIssueCounts;
}

interface DashboardTodosProps extends DashboardProgressProps {
  toIssues: (filter: string) => void;
}

export function DashboardTodos({ demo, counts, toIssues }: DashboardTodosProps) {
  return (
    <Panel
      title="护航待办"
      description="先处理复发，再清理验证积压"
      action={<Badge tone="primary">全局</Badge>}
      className="flex flex-col"
    >
      <div className="-mt-1 flex-1">
        {[
          {
            name: '再次复发',
            desc: '需要判断修改是否无效',
            count: counts.recurring,
            filter: 'recurring',
            tone: 'text-destructive',
          },
          {
            name: '已应用待验证',
            desc: '等待自然流量或人工确认',
            count: counts.applied,
            filter: 'applied',
            tone: 'text-warning',
          },
          {
            name: '待审核建议',
            desc: '已生成方案，需人工审核',
            count: counts.open,
            filter: 'open',
            tone: 'text-primary',
          },
        ].map((item) => (
          <Button
            key={item.name}
            variant="ghost"
            className="h-auto w-full justify-start gap-4 rounded-none border-b border-border/70 px-1 py-3 text-left"
            onClick={() => toIssues(item.filter)}
          >
            <span className={`w-7 text-center text-2xl font-semibold tabular-nums ${item.tone}`}>{item.count}</span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs">{item.name}</span>
              <span className="mt-1 block text-[10px] font-normal text-muted-foreground">{item.desc}</span>
            </span>
            <ArrowRight size={13} className="text-muted-foreground" />
          </Button>
        ))}
      </div>
      <Button className="mt-4 w-full" onClick={() => demo.navigate('workbench')}>
        进入护航工作台
        <ArrowRight size={14} />
      </Button>
    </Panel>
  );
}

export function DashboardProgress({ demo, counts }: DashboardProgressProps) {
  const { affectedRuns, recommendations, completedCount } = useWorkbenchSummary(demo);
  return (
    <Panel
      title="护航处置进展"
      description="从发现到验证，让每一次优化有据可循"
      action={<span className="text-[11px] text-muted-foreground">过程计数，非同一批次转化率</span>}
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          {
            label: '异常问题',
            value: demo.issues.length,
            hint: `${affectedRuns} 次受影响运行`,
            icon: CircleAlert,
            page: 'issues',
            filter: 'all',
          },
          {
            label: '建议生成',
            value: recommendations,
            hint: `${counts.open} 条等待人工审核`,
            icon: Sparkles,
            page: 'issues',
            filter: 'open',
          },
          {
            label: '修改已应用',
            value: counts.applied,
            hint: '仅表示执行成功，不等于修复成功',
            icon: GitBranch,
            page: 'issues',
            filter: 'applied',
          },
          {
            label: '人工验证',
            value: completedCount,
            hint: '验证有效后可沉淀为经验',
            icon: Check,
            page: 'issues',
            filter: 'verified',
          },
        ].map((item, index) => (
          <Button
            variant="ghost"
            key={item.label}
            className="h-auto justify-start gap-3 rounded-lg bg-muted/45 p-4 text-left"
            onClick={() => {
              demo.navigate(item.page as PageId);
              demo.setIssueFilter(item.filter);
            }}
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-border bg-card text-primary">
              <item.icon size={17} />
            </span>
            <span>
              <span className="text-[11px] font-normal text-muted-foreground">
                0{index + 1} / {item.label}
              </span>
              <strong className="my-1 block text-2xl font-semibold tabular-nums">{item.value}</strong>
              <span className="block text-[10px] font-normal text-muted-foreground">{item.hint}</span>
            </span>
          </Button>
        ))}
      </div>
    </Panel>
  );
}
