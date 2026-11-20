import { Button } from '@/components/ui/Button';
import {
  formatDashboardCount as count,
  formatDashboardDuration,
  formatDashboardRate,
} from '@/domain/taskEscortWorkspace/dashboard';
import type { DashboardOverview, DashboardResource } from '@/domain/taskEscortWorkspace/dashboardTypes';
import { DashboardReadState } from './DashboardControls';

const panel = 'min-w-0 rounded-xl border border-border bg-card p-5 text-card-foreground';
export function DashboardSummary({
  resource,
  rangeLabel,
  onWorkbench,
}: {
  resource: DashboardResource<DashboardOverview>;
  rangeLabel: string;
  onWorkbench: () => void;
}) {
  return (
    <>
      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section className={`${panel} bg-muted/20`} aria-label="运行健康">
          <h2 className="text-base font-semibold">运行健康</h2>
          <p className="mt-1 text-xs text-muted-foreground">{rangeLabel} · 当前权限范围</p>
          <DashboardReadState resource={resource} title="运行健康">
            {(data) => {
              const delta =
                data.successRate !== null &&
                data.successRate !== undefined &&
                data.prevSuccessRate !== null &&
                data.prevSuccessRate !== undefined
                  ? (data.successRate - data.prevSuccessRate) * 100
                  : null;
              const previous = data.prevMachineDurationP50;
              const durationDelta =
                data.machineDurationP50 !== null &&
                data.machineDurationP50 !== undefined &&
                previous !== null &&
                previous !== undefined &&
                previous > 0
                  ? ((data.machineDurationP50 - previous) / previous) * 100
                  : null;
              return (
                <>
                  {data.totalRuns === 0 && <p className="mt-4 text-sm text-muted-foreground">所选周期暂无运行</p>}
                  <dl className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {[
                      {
                        label: '运行数',
                        value: count(data.totalRuns),
                        note: `${rangeLabel}全部运行`,
                        tone: 'text-primary',
                      },
                      {
                        label: '运行成功率',
                        value: formatDashboardRate(data.successRate),
                        note: '成功 / 全部已结束运行',
                        tone: 'text-success',
                        delta:
                          delta === null || delta === undefined
                            ? '暂无可比上期'
                            : `较上期 ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} 个百分点`,
                      },
                      {
                        label: '异常结束',
                        value: count(data.failedCount),
                        note: '失败、终止或取消',
                        tone: 'text-destructive',
                      },
                      {
                        label: '成功运行耗时P50',
                        value: formatDashboardDuration(data.machineDurationP50),
                        note: '成功运行端到端耗时（含人工等待）',
                        tone: 'text-foreground',
                        delta:
                          durationDelta === null || durationDelta === undefined
                            ? '暂无可比上期'
                            : `较上期 ${durationDelta >= 0 ? '+' : ''}${durationDelta.toFixed(1)}%`,
                      },
                    ].map((metric) => (
                      <div key={metric.label} className="min-w-0 rounded-lg border border-border bg-card px-3 py-4">
                        <dt className="text-xs text-muted-foreground">{metric.label}</dt>
                        <dd className={`mt-2 break-all text-2xl font-semibold tabular-nums ${metric.tone}`}>
                          {metric.value}
                        </dd>
                        <p className="mt-2 text-xs leading-5 text-muted-foreground">{metric.note}</p>
                        {metric.delta && <p className="mt-1 text-xs text-muted-foreground">{metric.delta}</p>}
                      </div>
                    ))}
                  </dl>
                  <dl className="mt-4 grid grid-cols-2 gap-2 border-t border-border pt-4 sm:grid-cols-4">
                    {(
                      [
                        ['running', '运行中'],
                        ['waiting', '等待中'],
                        ['blocked', '阻塞'],
                        ['queued', '排队中'],
                      ] as const
                    ).map(([key, label]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs"
                      >
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd className="font-semibold tabular-nums">{count(data.statusDistribution?.[key])}</dd>
                      </div>
                    ))}
                  </dl>
                </>
              );
            }}
          </DashboardReadState>
        </section>
        <aside className={panel} aria-label="护航待办">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">护航待办</h2>
              <p className="mt-1 text-xs text-muted-foreground">先处理复发，再清理验证积压</p>
            </div>
            <span className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">当前权限范围</span>
          </div>
          <DashboardReadState resource={resource} title="护航待办">
            {({ evolution }) => {
              const e = evolution?.available ? evolution : null;
              return (
                <>
                  {!e && <p className="mt-4 text-xs text-muted-foreground">护航统计能力暂不可用</p>}
                  <dl className="mt-4 divide-y divide-border border-y border-border">
                    {[
                      {
                        label: '再次复发',
                        value: e?.recurrenceDetectedCount,
                        note: '需要判断修改是否无效',
                        tone: 'text-destructive',
                      },
                      {
                        label: '已应用待验证',
                        value: e?.appliedUnverifiedCount,
                        note: '等待自然流量或人工确认',
                        tone: 'text-warning',
                      },
                      {
                        label: '新增建议',
                        value: e?.suggestionCount,
                        note: `${rangeLabel}生成，需人工审核`,
                        tone: 'text-foreground',
                      },
                      {
                        label: '异常问题',
                        value: e?.issueClusterCount,
                        note: `${rangeLabel}按问题签名聚合`,
                        tone: 'text-foreground',
                      },
                    ].map((item) => (
                      <div key={item.label} className="flex items-center gap-3 py-3">
                        <dd className={`min-w-8 text-right text-xl font-semibold tabular-nums ${item.tone}`}>
                          {count(item.value)}
                        </dd>
                        <div className="min-w-0">
                          <dt className="text-xs font-medium">{item.label}</dt>
                          <p className="mt-1 text-xs text-muted-foreground">{item.note}</p>
                        </div>
                      </div>
                    ))}
                  </dl>
                </>
              );
            }}
          </DashboardReadState>
          <Button className="mt-4 w-full" onClick={onWorkbench}>
            进入任务护航
          </Button>
        </aside>
      </div>
      <section className={panel} aria-label="护航处理进度">
        <h2 className="text-sm font-semibold">护航处理进度</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          过程计数，不是同一批次的转化率；修改已应用不等于修复成功
        </p>
        <DashboardReadState resource={resource} title="护航处理进度">
          {({ evolution }) => {
            const e = evolution?.available ? evolution : null;
            return (
              <>
                <ol className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="护航处理步骤">
                  {[
                    {
                      label: '异常问题',
                      value: e?.issueClusterCount,
                      hint: `${count(e?.diagnosedRunCount)} 个受影响运行`,
                    },
                    { label: '建议生成', value: e?.suggestionCount, hint: '等待审核' },
                    { label: '修改已应用', value: e?.applicationSucceededCount, hint: '仅代表执行成功' },
                    {
                      label: '人工验证',
                      value: e?.verificationAvailable ? e.verifiedCount : null,
                      hint: e?.verificationAvailable ? rangeLabel : '数据能力待升级',
                    },
                  ].map((stage, index) => (
                    <li key={stage.label} className="min-w-0 rounded-lg bg-muted/50 p-4">
                      <p className="text-xs text-muted-foreground">步骤 {index + 1}</p>
                      <p className="mt-1 text-2xl font-semibold tabular-nums">{count(stage.value)}</p>
                      <p className="mt-1 text-xs font-medium">{stage.label}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{stage.hint}</p>
                    </li>
                  ))}
                </ol>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
                  <span>
                    应用执行：{count(e?.applicationSucceededCount)}/{count(e?.applicationAttemptCount)} 次成功
                  </span>
                  <span>经验需独立审核，不由建议采纳或应用自动生成</span>
                </div>
              </>
            );
          }}
        </DashboardReadState>
      </section>
    </>
  );
}
