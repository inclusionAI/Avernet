import { useIssuePresentation } from '@/hooks/taskEscortWorkspace/useIssuePresentation';
import { ArrowRight, CircleCheck, GitBranch, Lightbulb, ShieldAlert } from 'lucide-react';
import { IssueDetail } from './IssueDetail';
import { IssueEmpty, SeverityBadge } from './Issues.parts';
import type { ViewProps } from './types';
import { Badge, Button, Panel, SearchField, SelectField, StatusBadge } from './ui';

export function Issues({ demo }: ViewProps) {
  const { summary, issues, clear } = useIssuePresentation(demo);
  const statusOptions = [
    { value: 'all', label: '全部异常', count: demo.issues.length },
    { value: 'open', label: '待处理', count: summary.open },
    { value: 'recurring', label: '复发', count: summary.recurring },
    { value: 'applied', label: '已应用 · 待验证', count: summary.applied },
    { value: 'verified', label: '已验证', count: summary.verified },
  ];

  return (
    <div className="space-y-6" data-testid="issues-view" data-detail-focus-fallback tabIndex={-1}>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">异常与建议</h2>
          <p className="mt-2 text-sm text-muted-foreground">定位根因、审阅变更，由你掌握修复的最后一步。</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <ShieldAlert className="size-3.5 text-destructive" />
            {summary.open + summary.recurring} 项待处理
          </span>
          <span className="flex items-center gap-1.5">
            <CircleCheck className="size-3.5 text-success" />
            {summary.verified} 项已验证
          </span>
        </div>
      </header>
      <Panel
        title="异常列表"
        description="按状态与工作流筛选，查看每条建议背后的证据"
        action={
          <Badge tone="outline" className="text-xs font-normal">
            人工审阅优先
          </Badge>
        }
      >
        <div className="-mx-5 mb-4 border-b border-border px-5">
          <div className="flex gap-1 overflow-x-auto pb-3" role="tablist" aria-label="异常状态筛选">
            {statusOptions.map((option) => (
              <Button
                key={option.value}
                variant={demo.issueFilter === option.value ? 'secondary' : 'ghost'}
                size="sm"
                role="tab"
                id={`issues-tab-${option.value}`}
                aria-selected={demo.issueFilter === option.value}
                aria-controls="issue-results"
                className={`shrink-0 gap-2 text-xs ${
                  demo.issueFilter === option.value
                    ? 'bg-primary/10 text-primary hover:bg-primary/15'
                    : 'text-muted-foreground'
                }`}
                onClick={() => demo.setIssueFilter(option.value)}
              >
                {option.label}
                <span className="tabular-nums opacity-70">{option.count}</span>
              </Button>
            ))}
          </div>
        </div>
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <div className="min-w-48 flex-1">
            <SearchField value={demo.query} onChange={demo.setQuery} placeholder="搜索异常、工作流或日志证据…" />
          </div>
          <SelectField
            label="异常工作流筛选"
            value={demo.workflowId || 'all'}
            onChange={demo.setWorkflowId}
            options={[
              { value: 'all', label: '全部工作流' },
              ...demo.workflows.map((workflow) => ({
                value: workflow.id,
                label: workflow.name,
              })),
            ]}
          />
          {(demo.query || demo.issueFilter !== 'all' || demo.workflowId !== 'all') && (
            <Button variant="ghost" size="sm" onClick={clear}>
              清除筛选
            </Button>
          )}
        </div>
        <div id="issue-results" role="tabpanel" aria-labelledby={`issues-tab-${demo.issueFilter || 'all'}`}>
          <p className="mb-3 text-xs text-muted-foreground" role="status" aria-live="polite">
            共 {issues.length} 条异常{demo.query && `，关键词「${demo.query}」`}
          </p>
          {issues.length ? (
            <div className="space-y-3">
              {issues.map((issue) => (
                <article
                  key={issue.id}
                  className="rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary/25"
                  data-testid="issue-card"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <SeverityBadge severity={issue.severity} />
                        <StatusBadge status={issue.status} />
                        <span className="mono text-[11px] text-muted-foreground">{issue.id}</span>
                        <Badge tone="outline" className="text-[10px] font-normal text-muted-foreground">
                          {issue.category}
                        </Badge>
                      </div>
                      <h3 className="text-base font-semibold leading-7">{issue.title}</h3>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      aria-label={`查看异常详情：${issue.title}`}
                      onClick={() => demo.setSelectedIssue(issue.id)}
                    >
                      查看详情
                      <ArrowRight className="ml-2 size-3.5" />
                    </Button>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">{issue.description}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <GitBranch className="size-3.5" />
                      {issue.workflowName}
                    </span>
                    <span>异常节点：{issue.node}</span>
                    <span>
                      影响 <strong className="font-medium text-foreground">{issue.affectedRuns}</strong> 次运行
                    </span>
                  </div>
                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    <div className="rounded-lg bg-muted/40 p-3">
                      <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">关键证据</p>
                      <code className="line-clamp-2 break-all text-xs leading-6">
                        {issue.evidence[0] || issue.rootCause}
                      </code>
                    </div>
                    <div className="rounded-lg border border-primary/10 bg-primary/[0.025] p-3">
                      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-primary">
                        <Lightbulb className="size-3" />
                        建议方向
                      </p>
                      <p className="line-clamp-2 text-xs leading-6 text-muted-foreground">{issue.recommendation}</p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <IssueEmpty onClear={clear} />
          )}
        </div>
      </Panel>
      <div className="flex gap-2 text-xs leading-6 text-muted-foreground">
        <Lightbulb className="mt-1 size-3.5 shrink-0 text-primary" />
        <p>处理路径：审阅证据 → 确认应用 → 人工验证 → 手动保存为经验。演示不会调用后端或修改真实任务。</p>
      </div>
      <IssueDetail demo={demo} />
    </div>
  );
}
