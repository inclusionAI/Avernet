import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import type { DashboardTableFilter } from '@/domain/taskEscortWorkspace/dashboard';
import {
  dashboardSortLabels,
  formatDashboardCount,
  formatDashboardCycle,
  formatDashboardDate,
  formatDashboardDuration,
  formatDashboardRate,
  joinDashboardWorkflows,
  selectDashboardWorkflows,
} from '@/domain/taskEscortWorkspace/dashboard';
import type {
  DashboardResource,
  DashboardWidgetRange,
  DashboardWorkflowHealth,
  DashboardWorkflowReleases,
  DashboardWorkflowSort,
  DashboardWorkflowStatus,
} from '@/domain/taskEscortWorkspace/dashboardTypes';
import { useState } from 'react';
import { DashboardSelect, DashboardTooltip, DashboardWidgetSelector } from './DashboardControls';

const statusLabels: Record<DashboardWorkflowStatus, string> = {
  released: '已发布',
  testing: '测试',
  unpublished: '未发布',
  unknown: '未知',
};
const columns = [
  '工作流',
  '状态',
  '运行数',
  '失败数',
  '运行成功率',
  '自愈成功率',
  '耗时P50',
  '部署次数',
  '研发周期',
  '最近部署',
  '批均成功率',
];

export function DashboardWorkflows({
  health,
  releases,
  range,
  globalLabel,
  windowLabel,
  onRangeChange,
  onWorkflow,
}: {
  health: DashboardResource<DashboardWorkflowHealth>;
  releases: DashboardResource<DashboardWorkflowReleases>;
  range: DashboardWidgetRange;
  globalLabel: string;
  windowLabel: string;
  onRangeChange: (range: DashboardWidgetRange) => void;
  onWorkflow: (id: string) => void;
}) {
  const [filter, setFilter] = useState<DashboardTableFilter>({
    query: '',
    status: 'all',
    sort: 'completionAsc',
    page: 1,
  });
  const update = (patch: Partial<DashboardTableFilter>) =>
    setFilter((previous) => ({ ...previous, ...patch, page: 1 }));
  const healthRows =
    !health.loading && !health.error && health.data && health.data.available !== false ? health.data.workflows : null;
  const releaseRows = !releases.loading && !releases.error && releases.data?.available ? releases.data.workflows : null;
  const result = selectDashboardWorkflows(joinDashboardWorkflows(healthRows, releaseRows), filter);
  const loading = health.loading && releases.loading;
  const notices = [
    { title: '运行健康', resource: health, unavailable: health.data?.available === false },
    { title: '发布统计', resource: releases, unavailable: releases.data?.available === false },
  ];
  return (
    <section
      className="min-w-0 rounded-xl border border-border bg-card p-5 text-card-foreground"
      aria-label="工作流列表"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">工作流列表</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {windowLabel}运行与部署数据 · 研发周期/最近部署为全周期口径 · 默认按运行成功率从低到高
          </p>
        </div>
        <DashboardWidgetSelector title="工作流列表" value={range} globalLabel={globalLabel} onChange={onRangeChange} />
      </div>
      <div className="my-4 flex flex-wrap items-center gap-3">
        <DashboardSelect
          label="工作流状态"
          value={filter.status}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'released', label: '已发布' },
            { value: 'notReleased', label: '未发布' },
          ]}
          onChange={(status) => update({ status })}
        />
        <DashboardSelect
          label="工作流排序"
          value={filter.sort}
          options={Object.entries(dashboardSortLabels).map(([value, label]) => ({
            value: value as DashboardWorkflowSort,
            label,
          }))}
          onChange={(sort) => update({ sort })}
        />
        <Input
          aria-label="搜索工作流"
          placeholder="搜索工作流名/ID"
          value={filter.query}
          onChange={(event) => update({ query: event.target.value })}
          className="h-8 w-full min-w-0 sm:w-56"
        />
        <span className="text-xs text-muted-foreground sm:ml-auto">{result.total} 个工作流</span>
      </div>
      <div className="space-y-2" aria-live="polite">
        {notices.map(({ title, resource, unavailable }) =>
          resource.error ? (
            <div
              key={title}
              role="alert"
              className="flex flex-wrap items-center gap-2 rounded-lg bg-destructive/5 px-3 py-2 text-xs"
            >
              <span className="break-words text-destructive">
                {title}读取失败：{resource.error}
              </span>
              <Button size="sm" variant="outline" onClick={resource.retry}>
                重试{title}
              </Button>
            </div>
          ) : unavailable ? (
            <p key={title} className="rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              {title}能力暂不可用，对应指标显示 —
            </p>
          ) : resource.loading && !loading ? (
            <p key={title} role="status" className="text-xs text-muted-foreground">
              {title}加载中，对应旧数据已清除
            </p>
          ) : null,
        )}
      </div>
      <p className="my-3 text-xs text-muted-foreground">
        失败数仅统计 failed；异常结束包含失败、终止或取消。接口有返回上限，未匹配指标为未知（—），不推断为零。
      </p>
      {loading ? (
        <div role="status" aria-label="工作流列表加载中" className="space-y-3">
          <span className="sr-only">工作流列表加载中</span>
          {[0, 1, 2, 3].map((item) => (
            <Skeleton.Block key={item} className="h-10 w-full" />
          ))}
        </div>
      ) : result.rows.length === 0 ? (
        <Empty
          compact
          title={
            (healthRows === null || healthRows === undefined) && (releaseRows === null || releaseRows === undefined)
              ? '工作流指标暂不可用'
              : '无匹配工作流'
          }
          description="仅展示真实接口返回的工作流，不使用运行样本补造统计"
        />
      ) : (
        <div
          className="max-w-full overflow-x-auto rounded-lg border border-border"
          tabIndex={0}
          role="region"
          aria-label="工作流指标表，可横向滚动"
        >
          <table className="w-full min-w-[1120px] text-xs" aria-label="工作流列表">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                {columns.map((label, index) => (
                  <th
                    key={label}
                    scope="col"
                    className={`whitespace-nowrap px-3 py-3 font-medium ${index === 0 ? 'text-left' : 'text-right'}`}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {result.rows.map((row) => (
                <tr key={row.workflowId} className="hover:bg-muted/30">
                  <td className="max-w-64 px-3 py-3">
                    <DashboardTooltip label={row.workflowTitle}>
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto max-w-full justify-start px-0 py-0 text-left"
                        onClick={() => onWorkflow(row.workflowId)}
                        aria-label={`查看工作流 ${row.workflowTitle}`}
                      >
                        <span className="truncate">{row.workflowTitle}</span>
                      </Button>
                    </DashboardTooltip>
                    <DashboardTooltip label={row.workflowId}>
                      <p className="mt-1 truncate font-mono text-muted-foreground" tabIndex={0}>
                        {row.workflowId}
                      </p>
                    </DashboardTooltip>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-right">
                    <span
                      className={`rounded-full px-2 py-1 ${
                        row.status === 'released' ? 'bg-success/10 text-success' : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {statusLabels[row.status]}
                    </span>
                  </td>
                  {[
                    formatDashboardCount(row.runCount),
                    formatDashboardCount(row.failedCount),
                    formatDashboardRate(row.completionSuccessRate),
                    formatDashboardRate(row.selfHealSuccessRate),
                    formatDashboardDuration(row.machineDurationP50),
                    formatDashboardCount(row.deployCount),
                    formatDashboardCycle(row.devCycleMs),
                    formatDashboardDate(row.lastDeployAt),
                    formatDashboardRate(row.firstRunSuccessRate),
                  ].map((value, index) => (
                    <td key={index} className="whitespace-nowrap px-3 py-3 text-right tabular-nums">
                      {value}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && result.total > 10 && (
        <nav
          aria-label="工作流分页"
          className="mt-4 flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground"
        >
          <span>
            第 {result.page} / {result.pages} 页
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={result.page <= 1}
            onClick={() => setFilter((previous) => ({ ...previous, page: result.page - 1 }))}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={result.page >= result.pages}
            onClick={() => setFilter((previous) => ({ ...previous, page: result.page + 1 }))}
          >
            下一页
          </Button>
        </nav>
      )}
    </section>
  );
}
