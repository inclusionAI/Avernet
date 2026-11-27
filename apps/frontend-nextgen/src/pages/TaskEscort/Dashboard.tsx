import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { DashboardScope } from '@/domain/taskEscortWorkspace/dashboardTypes';
import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { unresolvedDashboardScope, useTaskEscortDashboard } from '@/hooks/taskEscortWorkspace/useTaskEscortDashboard';
import { RefreshCw } from 'lucide-react';
import { DashboardHotspots, DashboardTrend } from './dashboard/DashboardCharts';
import { DashboardSummary } from './dashboard/DashboardSummary';
import { DashboardWorkflows } from './dashboard/DashboardWorkflows';

/** The legacy prop name is kept for entry compatibility; no demo data is consumed. */
export function Dashboard({
  demo,
  scope = unresolvedDashboardScope,
}: {
  demo: TaskEscortWorkspaceController;
  scope?: DashboardScope;
}) {
  const dashboard = useTaskEscortDashboard(scope, demo.period);
  const ready = scope.ready && !scope.loading && !scope.error;
  return (
    <div className="min-w-0 space-y-5">
      <PageHeader
        title="管理员数据大盘"
        description="全局运行健康、护航进展与风险工作流"
        actions={
          <div className="flex max-w-full flex-wrap items-center gap-2">
            <div
              role="group"
              aria-label="全局时间范围"
              className="flex flex-wrap gap-1 rounded-lg border border-border bg-muted/40 p-1"
            >
              {(
                [
                  ['today', '今天'],
                  ['yesterday', '昨天'],
                  ['7d', '7天'],
                  ['30d', '30天'],
                ] as const
              ).map(([key, label]) => (
                <Button
                  key={key}
                  size="sm"
                  variant={dashboard.range === key ? 'default' : 'ghost'}
                  aria-pressed={dashboard.range === key}
                  disabled={!ready}
                  onClick={() => demo.setPeriod(key)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <Button
              size="sm"
              variant="outline"
              aria-label="刷新大盘"
              disabled={!ready}
              onClick={dashboard.refresh}
              leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
            >
              刷新
            </Button>
          </div>
        }
      />
      {!ready ? (
        scope.error ? (
          <div role="alert">
            <Empty
              title="身份或空间读取失败"
              description={scope.error}
              action={
                scope.retry ? (
                  <Button variant="outline" onClick={scope.retry}>
                    重试身份读取
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : scope.loading ? (
          <div role="status" aria-label="正在确认身份">
            <p className="text-sm text-muted-foreground">正在确认身份，暂不读取全局数据</p>
            <Skeleton.Block className="mt-4 h-40 w-full" />
          </div>
        ) : (
          <Empty title="等待身份就绪" description="身份与空间就绪后读取管理员全局数据" />
        )
      ) : dashboard.forbidden ? (
        <div role="alert">
          <Empty
            title="数据大盘仅管理员可见"
            description="当前身份没有全局大盘权限；已为你保留「运行概览」，可继续查看单个工作流的运行记录。如需全局统计，请联系管理员申请权限。"
            action={
              <Button variant="outline" onClick={() => demo.navigate('runs')}>
                返回运行概览
              </Button>
            }
          />
        </div>
      ) : (
        <>
          <DashboardSummary
            resource={dashboard.overview}
            rangeLabel={dashboard.rangeLabel}
            onWorkbench={() => demo.navigate('workbench')}
          />
          <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <DashboardTrend
              resource={dashboard.trend}
              range={dashboard.widgetRanges.trend}
              globalLabel={dashboard.rangeLabel}
              onRangeChange={(value) => dashboard.setWidgetRange('trend', value)}
            />
            <DashboardHotspots
              resource={dashboard.hotspots}
              range={dashboard.widgetRanges.hotspots}
              globalLabel={dashboard.rangeLabel}
              onRangeChange={(value) => dashboard.setWidgetRange('hotspots', value)}
            />
          </div>
          <DashboardWorkflows
            key={scope.key}
            health={dashboard.health}
            releases={dashboard.releases}
            range={dashboard.widgetRanges.workflows}
            globalLabel={dashboard.rangeLabel}
            windowLabel={dashboard.windows.workflows.label}
            onRangeChange={(value) => dashboard.setWidgetRange('workflows', value)}
            onWorkflow={(id) => {
              demo.navigate('workbench');
              demo.setWorkflowId(id);
            }}
          />
        </>
      )}
    </div>
  );
}
