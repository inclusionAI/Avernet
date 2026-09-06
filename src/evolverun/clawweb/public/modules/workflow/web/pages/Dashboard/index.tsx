import { useState, useMemo, useCallback, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { dashboardApi, isDemoMode } from './api'
import { TimeRangeSelector } from './components/TimeRangeSelector'
import { KpiCard } from './components/KpiCard'
import { WorkflowFullTable } from './components/WorkflowFullTable'
import { ReleaseEfficiencySection } from './components/ReleaseEfficiencySection'
import { ReleaseQualityTrendChart } from './components/ReleaseQualityTrendChart'
import { FailureHotspotChart } from './components/FailureHotspotChart'
import { LoopyTrendChart } from './components/LoopyTrendChart'
import { MetricTrendModal } from './components/MetricTrendModal'
import { GuardianMetricDrawer, type GuardianMetricKind } from './components/GuardianMetricDrawer'
import { WidgetRangeSelector } from './components/WidgetRangeSelector'
import { resolveWidgetRange, type WidgetRange } from './components/widget-range'
import type { TimeRangeKey, MetricKey, TrendGranularity } from '../../types/dashboard'

type TabKey = 'efficiency' | 'guardian'

const DEMO_TABS: Array<{ key: TabKey; label: string; desc: string }> = [
  { key: 'guardian', label: '任务护航', desc: '全局运行健康与处置进展' },
  { key: 'efficiency', label: '研发效能', desc: '工作流交付与发布质量' },
]

// demo 模式在本次会话内固定(URL/env 触发整页重载),故取常量即可
const DEMO = isDemoMode()

export default function DashboardPage({ embedded = false }: { embedded?: boolean }) {
  const [timeRange, setTimeRange] = useState<TimeRangeKey>('7d')
  const [guardianMetric, setGuardianMetric] = useState<GuardianMetricKind | null>(null)
  const [qualityRange, setQualityRange] = useState<WidgetRange>('global')
  const [hotspotRange, setHotspotRange] = useState<WidgetRange>('global')
  const [workflowRange, setWorkflowRange] = useState<WidgetRange>('global')
  const [tab, setTab] = useState<TabKey>('guardian')
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000))
  const [trendModal, setTrendModal] = useState<{
    metric: MetricKey | null
    label: string
    gran: TrendGranularity
    naReason?: string
    l2Link?: string
  } | null>(null)
  useEffect(() => {
    const timer = window.setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  // 统一点钻:点任何 KPI 卡 = 弹趋势;metric=null 表示暂不可算(弹窗占位+列表入口)
  const openTrend = useCallback((
    metric: MetricKey | null,
    label: string,
    gran: TrendGranularity,
    naReason?: string,
    l2Link?: string,
  ) => {
    setTrendModal({ metric, label, gran, naReason, l2Link })
  }, [])

  // 页面 timeRange 的大白话口径文案(全页所有 KPI 数字都跟它走)
  const rangeLabel = timeRange === 'today' ? '今天' : timeRange === '7d' ? '近 7 天' : '近 30 天'

  const { from, to } = useMemo(() => {
    const daySec = 86400
    switch (timeRange) {
      case 'today': {
        const localStart = new Date(nowSec * 1000)
        localStart.setHours(0, 0, 0, 0)
        const startOfDay = Math.floor(localStart.getTime() / 1000)
        return { from: startOfDay, to: nowSec }
      }
      case '7d': return { from: nowSec - 7 * daySec, to: nowSec }
      case '30d': return { from: nowSec - 30 * daySec, to: nowSec }
    }
  }, [nowSec, timeRange])
  const globalRange = useMemo(() => ({ from, to, label: rangeLabel }), [from, to, rangeLabel])
  const qualityWindow = useMemo(() => resolveWidgetRange(qualityRange, globalRange), [qualityRange, globalRange])
  const hotspotWindow = useMemo(() => resolveWidgetRange(hotspotRange, globalRange), [hotspotRange, globalRange])
  const workflowWindow = useMemo(() => resolveWidgetRange(workflowRange, globalRange), [workflowRange, globalRange])

  // ── Queries ──
  const overviewQ = useQuery({
    queryKey: ['dashboard', 'overview', timeRange],
    queryFn: () => dashboardApi.overview(from, to),
    refetchInterval: 30_000,
  })
  const dailyTrendQ = useQuery({
    queryKey: ['dashboard', 'daily-trend', from, to],
    queryFn: () => dashboardApi.dailyTrend(from, to),
  })
  const qualityTrendQ = useQuery({
    queryKey: ['dashboard', 'daily-trend', qualityWindow.from, qualityWindow.to],
    queryFn: () => dashboardApi.dailyTrend(qualityWindow.from, qualityWindow.to),
  })
  const releaseQ = useQuery({
    queryKey: ['dashboard', 'release-efficiency', timeRange],
    queryFn: () => dashboardApi.releaseEfficiency(from, to),
  })
  const failureQ = useQuery({
    queryKey: ['dashboard', 'failure-hotspots', hotspotWindow.from, hotspotWindow.to],
    queryFn: () => dashboardApi.failureHotspots(hotspotWindow.from, hotspotWindow.to),
  })
  const releaseStatsQ = useQuery({
    queryKey: ['dashboard', 'workflow-release-stats', workflowWindow.from, workflowWindow.to],
    queryFn: () => dashboardApi.workflowReleaseStats(workflowWindow.from, workflowWindow.to),
  })
  const healthQ = useQuery({
    queryKey: ['dashboard', 'workflow-health', workflowWindow.from, workflowWindow.to],
    queryFn: () => dashboardApi.workflowHealth(workflowWindow.from, workflowWindow.to),
  })
  const guardianHealthQ = useQuery({
    queryKey: ['dashboard', 'workflow-health', from, to],
    queryFn: () => dashboardApi.workflowHealth(from, to),
  })

  const overview = overviewQ.data
  const evolution = overview?.evolution
  const trend = dailyTrendQ.data

  // 三主线派生量(守护效果共用)
  const compRate = overview?.successRate ?? null
  const compDelta = overview?.successRate != null && overview?.prevSuccessRate != null
    ? overview.successRate - overview.prevSuccessRate : null
  const healRate = overview?.selfHealSuccessRate ?? null
  const machineP50 = overview?.machineDurationP50 ?? null
  const machinePrev = overview?.prevMachineDurationP50 ?? null
  const machineDeltaS = machineP50 !== null && machinePrev !== null ? (machineP50 - machinePrev) / 1000 : null
  const machineSub = machineDeltaS !== null
    ? `P50 · 环比 ${machineDeltaS >= 0 ? '+' : ''}${machineDeltaS.toFixed(1)}s · 端到端`
    : 'P50 · 端到端'

  // 不可算指标占位:真实模式后端返 null → "—"+"暂不可算 · 需X";demo 模式走 mock 值
  const naNum = (v: number | null | undefined): number | string =>
    !DEMO && v == null ? '—' : v ?? 0
  const naSub = (v: number | null | undefined, hint: string, normal?: string) =>
    !DEMO && v == null ? `暂不可算 · 需${hint}` : normal
  // 研发效能的发布相关:SQLite 库无 workflow_deploy_history
  const deployNaSub = (v: number | null | undefined, normal?: string) =>
    naSub(v, 'MySQL deploy_history(当前库无此表)', normal)

  return (
    <div className={embedded ? 'px-6 py-5' : 'mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8'}>
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-slate-950">管理员数据大盘</h1>
            {DEMO && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200">示例数据</span>}
          </div>
          <p className="mt-1 text-sm text-slate-500">全局运行健康、护航进展与风险工作流</p>
        </div>
        <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
      </div>

      {/* ── 两模块大盘(真实模式默认;?demo=1 走 mock 预览)── */}
      <>
          {/* 模块切换 */}
          <div className="mb-6 inline-flex rounded-lg border border-slate-200 bg-slate-100/70 p-1">
            {DEMO_TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`rounded-md px-4 py-2 text-left text-sm transition ${
                  tab === t.key
                    ? 'bg-white text-slate-950 shadow-sm ring-1 ring-slate-200'
                    : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                <span className="font-medium">{t.label}</span>
                <span className={`ml-2 hidden text-[11px] sm:inline ${tab === t.key ? 'text-slate-500' : 'text-slate-400'}`}>{t.desc}</span>
              </button>
            ))}
          </div>

          {/* ───── ① 研发效能(交付能力)───── */}
          {tab === 'efficiency' && (
            <>
              {/* 交付与采用:3 卡,口径跟右上角周期走,全部可点看趋势 */}
              <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-gray-400">
                <span className="h-px flex-1 bg-gray-100" />交付与采用<span className="h-px flex-1 bg-gray-100" />
              </div>
              <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <KpiCard
                  icon="📋"
                  label="活跃工作流"
                  value={overview?.workflowCount ?? 0}
                  color="blue"
                  sub={`${rangeLabel}跑过的工作流`}
                  onClick={() => openTrend('activeWorkflows', '活跃工作流数', 'month')}
                />
                <KpiCard
                  icon="🚀"
                  label="上线工作流"
                  value={naNum(overview?.windowReleasedCount)}
                  color="violet"
                  sub={deployNaSub(overview?.windowReleasedCount, `${rangeLabel} deploy 过的工作流`)}
                  onClick={() => openTrend('releases', '上线工作流数', 'week')}
                />
                <KpiCard
                  icon="👥"
                  label="活跃用户(DAU)"
                  value={naNum(overview?.dau)}
                  color="indigo"
                  sub={overview ? `WAU ${overview.wau ?? 0}` : undefined}
                  onClick={() => openTrend('dau', '活跃用户', 'day')}
                />
              </div>

              {/* 发布效能:4 小卡全部可点看趋势(含交付拖期);明细则归并到下方工作流列表 */}
              <div className="mb-6">
                <ReleaseEfficiencySection
                  data={releaseQ.data}
                  onTrendClick={(m, label) => openTrend(m, label, 'week')}
                />
              </div>
              <div className="mb-6">
                <ReleaseQualityTrendChart from={from} to={to} globalRangeLabel={rangeLabel} />
              </div>

              {/* 工作流列表(运行+发布全景,分页;点行→L3) */}
              <div className="mb-6 rounded-xl bg-white p-5 shadow-sm">
                <div className="mb-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div><h3 className="text-sm font-semibold text-gray-900">工作流列表</h3><p className="mt-0.5 text-[11px] text-gray-400">{workflowWindow.label}运行与发布口径 · 研发周期/最近部署为全周期口径 · 点行进单工作流详情</p></div>
                    <WidgetRangeSelector ariaLabel="工作流列表时间范围" value={workflowRange} globalLabel={rangeLabel} onChange={setWorkflowRange} />
                  </div>
                </div>
                <WorkflowFullTable
                  healthRows={healthQ.data?.workflows ?? []}
                  releaseRows={releaseStatsQ.data?.available ? releaseStatsQ.data.workflows : null}
                  isLoading={healthQ.isLoading || releaseStatsQ.isLoading}
                  isError={healthQ.isError || releaseStatsQ.isError}
                  initialSort="deployDesc"
                />
              </div>
            </>
          )}

          {/* ───── ② 管理员任务护航 ───── */}
          {tab === 'guardian' && (
            <>
              <div className="mb-6 grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
                <section className="rounded-2xl border border-slate-200 bg-slate-50/40 p-5" aria-labelledby="global-health-heading">
                  <div className="mb-4 flex items-end justify-between gap-4">
                    <div>
                      <h2 id="global-health-heading" className="text-base font-semibold text-slate-950">全局运行健康</h2>
                      <p className="mt-1 text-xs text-slate-500">{rangeLabel} · 管理员全量视角</p>
                    </div>
                    <button onClick={() => setGuardianMetric('incomplete')} className="text-xs font-medium text-blue-600 hover:text-blue-700">查看异常结束</button>
                  </div>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    <KpiCard
                      label="运行数"
                      value={overviewQ.isPending ? '—' : (overview?.totalRuns ?? 0).toLocaleString()}
                      color="blue"
                      sub={`${rangeLabel}全部运行`}
                      onClick={() => setGuardianMetric('runs')}
                    />
                    <KpiCard
                      label="运行成功率"
                      value={compRate !== null ? `${(compRate * 100).toFixed(1)}%` : '—'}
                      color="emerald"
                      delta={compDelta !== null ? compDelta * 100 : null}
                      sub="成功 / 全部已结束运行"
                      onClick={() => setGuardianMetric('successRate')}
                    />
                    <KpiCard
                      label="异常结束"
                      value={overviewQ.isPending ? '—' : (overview?.failedCount ?? 0).toLocaleString()}
                      color="rose"
                      sub="失败、终止或取消"
                      onClick={() => setGuardianMetric('incomplete')}
                    />
                    <KpiCard
                      label="成功运行耗时 P50"
                      value={machineP50 !== null ? `${(machineP50 / 1000).toFixed(0)}s` : '—'}
                      color="violet"
                      sub={machineSub}
                      onClick={() => setGuardianMetric('duration')}
                    />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 border-t border-slate-200 pt-4 sm:grid-cols-4">
                    {([
                      ['running', '运行中'],
                      ['waiting', '等待中'],
                      ['blocked', '阻塞'],
                      ['queued', '排队中'],
                    ] as const).map(([status, label]) => (
                      <button key={status} type="button" onClick={() => setGuardianMetric(status)} className="flex items-center justify-between rounded-lg bg-white px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200 transition hover:border-blue-200 hover:text-blue-700">
                        <span>{label}</span>
                        <span className="font-semibold tabular-nums text-slate-950">{overviewQ.isPending ? '—' : overview?.statusDistribution?.[status] ?? 0}</span>
                      </button>
                    ))}
                  </div>
                </section>

                <aside className="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]" aria-labelledby="escort-todo-heading">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h2 id="escort-todo-heading" className="text-base font-semibold text-slate-950">护航待办</h2>
                      <p className="mt-1 text-xs text-slate-500">先处理复发，再清理验证积压</p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">全局</span>
                  </div>
                  <div className="mt-4 divide-y divide-slate-100 border-y border-slate-100">
                    {[
                      { label: '再次复发', value: evolution?.recurrenceDetectedCount ?? '—', note: '需要判断修改是否无效', tone: 'text-rose-700' },
                      { label: '已应用待验证', value: evolution?.appliedUnverifiedCount ?? '—', note: '等待自然流量或人工确认', tone: 'text-amber-700' },
                      { label: '新增建议', value: evolution?.available ? evolution.suggestionCount : '—', note: `${rangeLabel}生成，需人工审核`, tone: 'text-slate-900' },
                      { label: '异常问题', value: evolution?.available ? evolution.issueClusterCount ?? '—' : '—', note: `${rangeLabel}按问题签名聚合`, tone: 'text-slate-900' },
                    ].map((item) => (
                      <div key={item.label} className="flex items-center gap-3 py-2.5">
                        <span className={`w-8 text-right text-lg font-semibold tabular-nums ${item.tone}`}>{item.value}</span>
                        <div className="min-w-0">
                          <p className="text-xs font-medium text-slate-700">{item.label}</p>
                          <p className="truncate text-[11px] text-slate-400">{item.note}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Link to="/workflows/workspace" className="mt-4 inline-flex items-center justify-center rounded-lg bg-slate-950 px-3 py-2 text-xs font-medium text-white transition hover:bg-slate-800">
                    进入任务护航
                  </Link>
                </aside>
              </div>

              <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]" aria-labelledby="escort-progress-heading">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 id="escort-progress-heading" className="text-sm font-semibold text-slate-950">护航处理进度</h2>
                    <p className="mt-1 text-xs text-slate-500">过程计数，不是同一批次的转化率；修改已应用不等于修复成功</p>
                  </div>
                  <span className="text-[11px] text-slate-400">人工确认有效 {evolution?.verifiedCount ?? '—'} 条</span>
                </div>
                <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    { label: '异常问题', value: evolution?.available ? evolution.issueClusterCount ?? '—' : '—', hint: `${evolution?.diagnosedRunCount ?? 0} 个受影响运行` },
                    { label: '建议生成', value: evolution?.available ? evolution.suggestionCount : '—', hint: '等待审核' },
                    { label: '修改已应用', value: evolution?.available ? evolution.applicationSucceededCount : '—', hint: '仅代表执行成功' },
                    { label: '人工验证', value: evolution?.verificationAvailable ? evolution.verifiedCount ?? '—' : '—', hint: evolution?.verificationAvailable ? rangeLabel : '数据能力待升级' },
                  ].map((stage) => (
                    <div key={stage.label} className="relative rounded-xl bg-slate-50 px-4 py-3">
                      <p className="text-xl font-semibold tabular-nums text-slate-950">{stage.value}</p>
                      <p className="mt-1 text-xs font-medium text-slate-600">{stage.label}</p>
                      <p className="mt-0.5 text-[10px] text-slate-400">{stage.hint}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-3 text-[11px] text-slate-500">
                  <span>应用执行：{evolution?.applicationSucceededCount ?? 0}/{evolution?.applicationAttemptCount ?? 0} 次成功</span>
                  <span>经验需独立审核，不由建议采纳或应用自动生成</span>
                </div>
              </section>

              {(!DEMO && (healRate == null || overview?.onlineSuccessRate == null)) && (
                <div className="mb-6 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] text-slate-500">
                  数据能力待接入：自愈事件收口、线上与测试运行分轨。接入前不进入核心指标区。
                </div>
              )}

              <div className="mb-6 grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
                <LoopyTrendChart data={qualityTrendQ.data?.dates ?? []} isLoading={qualityTrendQ.isLoading} isError={qualityTrendQ.isError} range={qualityRange} globalRangeLabel={rangeLabel} onRangeChange={setQualityRange} />
                <FailureHotspotChart data={failureQ.data} isLoading={failureQ.isLoading} isError={failureQ.isError} range={hotspotRange} globalRangeLabel={rangeLabel} onRangeChange={setHotspotRange} />
              </div>

              {/* 工作流列表垫底:复用完整表格；周期统一跟随页面右上角 */}
              <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
                <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-950">工作流列表</h3>
                    <p className="mt-0.5 text-[11px] text-slate-400">
                      {workflowWindow.label}运行与发布数据 · 默认按运行成功率从低到高 · 点击行查看详情
                    </p>
                  </div>
                  <WidgetRangeSelector ariaLabel="工作流列表时间范围" value={workflowRange} globalLabel={rangeLabel} onChange={setWorkflowRange} />
                </div>
                <WorkflowFullTable
                  healthRows={healthQ.data?.workflows ?? []}
                  releaseRows={releaseStatsQ.data?.available ? releaseStatsQ.data.workflows : null}
                  isLoading={healthQ.isLoading || releaseStatsQ.isLoading}
                  isError={healthQ.isError || releaseStatsQ.isError}
                  initialSort="completionAsc"
                />
              </div>
            </>
          )}
        </>

      {guardianMetric && overview && (
        <GuardianMetricDrawer
          kind={guardianMetric}
          overview={overview}
          trend={trend?.dates ?? []}
          workflows={guardianHealthQ.data?.workflows ?? []}
          from={from}
          to={to}
          rangeLabel={rangeLabel}
          onClose={() => setGuardianMetric(null)}
        />
      )}

      {trendModal && (
        <MetricTrendModal
          open
          metric={trendModal.metric}
          label={trendModal.label}
          naReason={trendModal.naReason}
          l2Link={trendModal.l2Link}
          defaultGranularity={trendModal.gran}
          onClose={() => setTrendModal(null)}
        />
      )}
    </div>
  )
}

// ── Helpers ──
