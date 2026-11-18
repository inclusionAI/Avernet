import { useWorkbenchPresentation } from '@/hooks/taskEscortWorkspace/useWorkbenchPresentation';
import { ArrowRight, CircleCheck, GitBranch } from 'lucide-react';
import { IssueDetail } from './IssueDetail';
import { IssueEmpty, SeverityBadge } from './Issues.parts';
import type { ViewProps } from './types';
import { Badge, Button, Panel, SearchField, SelectField, StatusBadge } from './ui';
import { WorkbenchPriorities, WorkbenchWorkflows } from './Workbench.cards';

export function Workbench({ demo }: ViewProps) {
  const { tabs, activeTab, issues, clear } = useWorkbenchPresentation(demo);

  return (
    <div className="space-y-6" data-testid="workbench-view" data-detail-focus-fallback tabIndex={-1}>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">待办工作台</h2>
          <p className="mt-2 text-sm text-muted-foreground">聚焦需要你决策的事，其余交给任务护航。</p>
        </div>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CircleCheck className="size-3.5 text-success" />
          演示数据 · 无生产变更
        </span>
      </header>

      <WorkbenchPriorities demo={demo} />

      <Panel
        title={activeTab === 'verified' ? '已完成记录' : '异常待办'}
        description={
          activeTab === 'verified'
            ? '已完成人工验证的历史记录；保存经验仍需显式操作'
            : '从证据出发，按影响程度安排处理优先级'
        }
        action={
          <Badge tone="outline" className="font-normal">
            {issues.length} 项
          </Badge>
        }
      >
        <div className="-mx-5 mb-4 border-b border-border px-5">
          <div role="tablist" aria-label="待办与已完成状态" className="flex gap-1 overflow-x-auto pb-3">
            {tabs.map((tab) => (
              <Button
                key={tab.value}
                variant={activeTab === tab.value ? 'secondary' : 'ghost'}
                size="sm"
                role="tab"
                aria-selected={activeTab === tab.value}
                aria-controls="workbench-issues"
                id={`workbench-tab-${tab.value}`}
                className={`shrink-0 gap-2 text-xs ${
                  activeTab === tab.value ? 'bg-primary/10 text-primary hover:bg-primary/15' : 'text-muted-foreground'
                }`}
                onClick={() => demo.setIssueFilter(tab.value)}
              >
                {tab.label}
                <span className="tabular-nums opacity-70">{tab.count}</span>
              </Button>
            ))}
          </div>
        </div>
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <SearchField value={demo.query} onChange={demo.setQuery} placeholder="搜索异常、节点或证据…" />
          </div>
          <SelectField
            value={demo.workflowId || 'all'}
            onChange={demo.setWorkflowId}
            label="工作台工作流筛选"
            options={[
              { value: 'all', label: '全部工作流' },
              ...demo.workflows.map((workflow) => ({
                value: workflow.id,
                label: workflow.name,
              })),
            ]}
          />
          {(demo.query || demo.workflowId !== 'all') && (
            <Button variant="ghost" size="sm" onClick={clear}>
              重置
            </Button>
          )}
        </div>
        <div id="workbench-issues" role="tabpanel" aria-labelledby={`workbench-tab-${activeTab}`}>
          {issues.length ? (
            <div className="divide-y divide-border">
              {issues.map((issue) => (
                <article
                  key={issue.id}
                  className="group grid gap-3 py-5 lg:grid-cols-[1fr_auto] lg:items-center"
                  data-testid="triage-row"
                >
                  <div className="min-w-0 space-y-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={issue.severity} />
                      <StatusBadge status={issue.status} />
                      <span className="mono text-[11px] text-muted-foreground">{issue.id}</span>
                    </div>
                    <h3 className="text-sm font-semibold leading-6">{issue.title}</h3>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1.5">
                        <GitBranch className="size-3.5" />
                        {issue.workflowName}
                      </span>
                      <span>节点：{issue.node}</span>
                      <span>
                        影响 <strong className="font-medium text-foreground">{issue.affectedRuns}</strong> 次运行
                      </span>
                    </div>
                    <p className="flex gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
                      <span className="shrink-0 font-medium text-foreground">关键证据</span>
                      <span className="line-clamp-1 break-all">{issue.evidence[0] || issue.description}</span>
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="justify-self-start lg:ml-5"
                    aria-label={`查看异常：${issue.title}`}
                    onClick={() => demo.setSelectedIssue(issue.id)}
                  >
                    {activeTab === 'verified' ? '查看记录' : '查看建议'}
                    <ArrowRight className="ml-2 size-3.5" />
                  </Button>
                </article>
              ))}
            </div>
          ) : (
            <IssueEmpty onClear={clear} />
          )}
        </div>
        <p className="-mx-5 -mb-5 mt-2 rounded-b-xl border-t border-border bg-muted/20 px-5 py-3 text-xs text-muted-foreground">
          建议不会自动执行；应用后仍需人工验证，不会自动进入经验库。
        </p>
      </Panel>

      <WorkbenchWorkflows demo={demo} />
      <IssueDetail demo={demo} />
    </div>
  );
}
