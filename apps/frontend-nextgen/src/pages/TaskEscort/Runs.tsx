import { useRunPresentation } from '@/hooks/taskEscortWorkspace/useRunPresentation';
import { ArrowRight, ChevronLeft, ChevronRight, SearchX } from 'lucide-react';
import { formatDuration, formatStartedAt } from './formatting';
import { RunDetail } from './RunDetail';
import { RunMetrics } from './Runs.metrics';
import type { ViewProps } from './types';
import { Badge, Button, Empty, Panel, SearchField, SelectField, StatusBadge } from './ui';

export function Runs({ demo }: ViewProps) {
  const { filtered, pageCount, currentPage, rows, samples, clear, setPage } = useRunPresentation(demo);

  return (
    <div className="space-y-6" data-testid="runs-view" data-detail-focus-fallback tabIndex={-1}>
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">运行记录</h2>
        <p className="mt-2 text-sm text-muted-foreground">每一次执行，都有迹可循。从运行状态追溯到具体节点。</p>
      </header>
      <RunMetrics demo={demo} />
      <Panel
        title="全部运行"
        description="以下为可交互的演示运行样本；顶部指标为所选周期汇总"
        action={
          <Badge tone="outline" className="font-normal">
            {demo.runs.length} 条演示记录
          </Badge>
        }
      >
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <SearchField
              value={demo.query}
              onChange={(query) => {
                demo.setQuery(query);
                setPage(1);
              }}
              placeholder="搜索运行 ID、工作流、Bot 或节点…"
            />
          </div>
          <SelectField
            label="运行状态筛选"
            value={demo.runFilter || 'all'}
            onChange={(value) => {
              demo.setRunFilter(value);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'success', label: '运行成功' },
              { value: 'failed', label: '运行失败' },
              { value: 'running', label: '运行中' },
              { value: 'waiting', label: '等待中' },
              { value: 'cancelled', label: '已取消' },
            ]}
          />
          <SelectField
            label="运行工作流筛选"
            value={demo.workflowId || 'all'}
            onChange={(value) => {
              demo.setWorkflowId(value);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部工作流' },
              ...demo.workflows.map((workflow) => ({
                value: workflow.id,
                label: workflow.name,
              })),
            ]}
          />
          {(demo.query || demo.runFilter !== 'all' || demo.workflowId !== 'all') && (
            <Button variant="ghost" size="sm" onClick={clear}>
              重置
            </Button>
          )}
        </div>
        <div className="-mx-5 overflow-x-auto border-y border-border">
          <table className="w-full min-w-[940px] text-left text-xs">
            <caption className="sr-only">
              运行样本列表，共 {filtered.length} 条。每条记录可通过查看详情按钮打开节点时间线。
            </caption>
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                {['运行 ID / 工作流', '状态', 'Bot', '触发方式', '执行耗时', '开始时间', '操作'].map((label) => (
                  <th key={label} scope="col" className="whitespace-nowrap px-5 py-3 text-[11px] font-medium">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((run) => (
                <tr key={run.id} className="transition-colors hover:bg-muted/25" data-testid="run-row">
                  <td className="px-5 py-3.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto max-w-64 flex-col items-start gap-1 px-0 py-0 text-left hover:bg-transparent"
                      aria-label={`打开运行 ${run.id}`}
                      onClick={() => demo.setSelectedRun(run.id)}
                    >
                      <span className="mono text-xs font-medium text-primary">{run.id}</span>
                      <span className="max-w-full truncate text-[11px] font-normal text-muted-foreground">
                        {run.name}
                      </span>
                    </Button>
                  </td>
                  <td className="whitespace-nowrap px-5 py-3.5">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="max-w-40 truncate px-5 py-3.5">{run.bot}</td>
                  <td className="whitespace-nowrap px-5 py-3.5 text-muted-foreground">{run.trigger}</td>
                  <td className="whitespace-nowrap px-5 py-3.5 tabular-nums">
                    {run.status === 'waiting' ? '等待执行' : formatDuration(run.duration)}
                  </td>
                  <td className="mono whitespace-nowrap px-5 py-3.5 text-[11px] text-muted-foreground">
                    {formatStartedAt(run.startedAt)}
                  </td>
                  <td className="px-5 py-3.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 gap-1 px-0 text-xs text-primary hover:bg-transparent"
                      aria-label={`查看运行 ${run.id} 详情`}
                      onClick={() => demo.setSelectedRun(run.id)}
                    >
                      详情
                      <ArrowRight className="size-3" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && (
            <div role="status">
              <Empty
                title="没有匹配的运行记录"
                description={
                  demo.query || demo.runFilter !== 'all' || demo.workflowId !== 'all'
                    ? '调整状态、工作流或搜索条件后重试。'
                    : '运行记录由执行引擎执行工作流后产生；执行一次工作流后即可在此查看。'
                }
                icon={<SearchX className="size-5" />}
                action={
                  <Button variant="outline" size="sm" onClick={clear}>
                    清除筛选
                  </Button>
                }
              />
            </div>
          )}
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
            共 {filtered.length} 条
            {filtered.length
              ? ` · 展示 ${(currentPage - 1) * 10 + 1}–${Math.min(currentPage * 10, filtered.length)}`
              : ''}
            <span className="ml-3 hidden sm:inline">样本失败 {samples.failed} 次</span>
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              aria-label="上一页运行记录"
              disabled={currentPage <= 1}
              onClick={() => setPage(currentPage - 1)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="px-1 text-xs tabular-nums text-muted-foreground">
              {currentPage} / {pageCount}
            </span>
            <Button
              variant="outline"
              size="icon"
              aria-label="下一页运行记录"
              disabled={currentPage >= pageCount}
              onClick={() => setPage(currentPage + 1)}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </Panel>
      <RunDetail demo={demo} />
    </div>
  );
}
