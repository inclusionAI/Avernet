import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useTaskEscortLiveReadView } from '@/hooks/taskEscortWorkspace/useTaskEscortLiveReadView';
import type { TaskEscortWorkspaceState } from '@/hooks/useTaskEscortWorkspace';
import { Activity, ArrowUpRight, CircleAlert, Layers3, Search } from 'lucide-react';
import { LiveRunDetail } from './LiveRunDetail';
import { Panel } from './ui';

export function LiveReadView({ demo }: { demo: TaskEscortWorkspaceState }) {
  const view = useTaskEscortLiveReadView(demo);
  if (!demo.runs.length && !demo.workflows.length)
    return (
      <Empty
        title="暂无可访问的工作流或运行"
        description="当前接口未返回可访问的工作流或运行记录，请确认当前空间与访问权限，或稍后刷新。"
      />
    );
  return (
    <div className="space-y-5" tabIndex={-1} data-detail-focus-fallback>
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-xs leading-6 text-muted-foreground">
        当前身份可见的工作流与最近运行样本（最多 100 条），不是全局统计。未接入全量趋势、问题分析和生产写入。
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[
          { label: '可见工作流', value: demo.workflows.length, icon: Layers3 },
          { label: '返回运行样本', value: view.sample.count, icon: Activity },
          { label: '样本中运行失败', value: view.sample.failed, icon: CircleAlert },
          { label: '样本中运行中', value: view.sample.running, icon: Activity },
        ].map((metric) => (
          <section className="rounded-xl border border-border bg-card p-4" key={metric.label}>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{metric.label}</span>
              <metric.icon className="size-4" aria-hidden />
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums">{metric.value}</p>
          </section>
        ))}
      </div>
      <Panel title="运行样本" description="搜索、筛选与计数只作用于本次返回的样本。">
        <div className="mb-4 flex flex-wrap gap-2">
          <div className="relative min-w-0 flex-1 basis-48">
            <Search className="absolute left-3 top-3 size-3 text-muted-foreground" aria-hidden />
            <Input
              aria-label="搜索运行样本"
              placeholder="搜索运行 ID 或工作流"
              value={demo.query}
              onChange={(event) => demo.setQuery(event.target.value)}
              className="pl-8 text-xs"
            />
          </div>
          <Select value={demo.workflowId} onValueChange={demo.setWorkflowId}>
            <SelectTrigger aria-label="筛选工作流" className="w-full text-xs sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部工作流</SelectItem>
              {demo.workflows.map((workflow) => (
                <SelectItem key={workflow.id} value={workflow.id}>
                  {workflow.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={demo.runFilter} onValueChange={demo.setRunFilter}>
            <SelectTrigger aria-label="筛选运行状态" className="w-full text-xs sm:w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[
                ['all', '全部状态'],
                ['success', '运行成功'],
                ['failed', '运行失败'],
                ['running', '运行中'],
                ['waiting', '等待中'],
                ['cancelled', '已取消'],
                ['unknown', '未知状态'],
              ].map(([id, label]) => (
                <SelectItem key={id} value={id}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {!view.rows.length ? (
          <Empty
            compact
            title="没有匹配的运行样本"
            action={
              <Button variant="outline" onClick={view.resetFilters}>
                清除筛选
              </Button>
            }
          />
        ) : (
          <div className="max-w-full overflow-x-auto rounded-lg border border-border">
            <table className="demo-table">
              <thead>
                <tr>
                  <th>工作流 / 运行 ID</th>
                  <th>状态</th>
                  <th>耗时</th>
                  <th>开始时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <div className="font-medium">{run.name}</div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">{run.id}</div>
                    </td>
                    <td>
                      <Badge tone={run.tone}>{run.statusLabel}</Badge>
                    </td>
                    <td>{run.durationLabel}</td>
                    <td>{run.startedAtLabel}</td>
                    <td>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`查看运行 ${run.id}`}
                        onClick={() => demo.setSelectedRun(run.id)}
                      >
                        详情
                        <ArrowUpRight className="ml-1 size-3" aria-hidden />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          匹配 {view.rows.length} 条 / 已返回 {view.sample.count} 条 · 未返回的记录不计入
        </p>
      </Panel>
      {demo.page === 'dashboard' && !!demo.workflows.length && (
        <Panel title="可见工作流">
          <div className="grid gap-3 md:grid-cols-2">
            {demo.workflows.map((workflow) => (
              <article key={workflow.id} className="min-w-0 rounded-lg border border-border p-4 text-xs">
                <h2 className="truncate font-medium">{workflow.name}</h2>
                <p className="mt-2 break-all font-mono text-muted-foreground">{workflow.id}</p>
                <p className="mt-3 text-muted-foreground">本次样本中 {workflow.runCount} 次运行，不代表历史总量</p>
                <Button
                  variant="link"
                  size="sm"
                  className="mt-2 px-0"
                  onClick={() => {
                    demo.navigate('runs');
                    demo.setWorkflowId(workflow.id);
                  }}
                >
                  查看运行样本
                  <ArrowUpRight className="ml-1 size-3" aria-hidden />
                </Button>
              </article>
            ))}
          </div>
        </Panel>
      )}
      <LiveRunDetail demo={demo} />
    </div>
  );
}
