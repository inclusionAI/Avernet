/**
 * Dashboard 目标形态 demo 数据(git 分支 clawmind_insight)。
 *
 * 这些 fixture 用于在发布机制/业务场景能力落地前,先把"建成后的大盘"可视化出来。
 * 数字锚定 spec §6 周报样例(DAU 312、上线 47、线上成功率 96.4%、覆盖 9 线、折算 86 人天、
 * 5 次发布/100%/无回滚、risk-review-pipeline 420/98% 等),其余由确定性生成器补足,
 * 不用 Math.random,保证每次刷新画面稳定。
 */

import type {
  IDashboardOverview,
  IDashboardDailyTrend,
  IDashboardDurationDistribution,
  IDashboardTopWorkflows,
  IDashboardSubsystemSummary,
  IDashboardSceneBreakdown,
  IDashboardReleaseEfficiency,
  IDashboardReleaseQualityTrend,
  IWorkflowReleaseStats,
  IWorkflowReleaseStatRow,
  IDashboardFailureHotspots,
  IDashboardMetricTrend,
  MetricKey,
  TrendGranularity,
  IDailyTrendPoint,
  IWorkflowHealthRow,
  IWorkflowHealthResponse,
  IWorkflowMetricsResponse,
  IWorkflowNodeHealthRow,
} from '../../types/dashboard'

// 确定性"伪随机":基于索引的 sin,稳定且 looks alive
function wave(i: number, seed = 1, amp = 1): number {
  return Math.sin(i * 0.7 + seed) * 0.5 * amp + Math.sin(i * 0.21 + seed * 2.3) * 0.5 * amp
}
function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n))
}
function round(n: number, d = 0): number {
  const f = 10 ** d
  return Math.round(n * f) / f
}

function daysBetween(from: number, to: number): number {
  return Math.max(1, Math.round((to - from) / 86400))
}
function dateLabel(offsetFromEnd: number): string {
  // to 默认按"今天"对齐,offsetFromEnd=0 是今天
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - offsetFromEnd)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ── 基线常量(锚定 spec §6)──
const BASE = {
  workflowCount: 54,
  releasedWorkflowCount: 47,
  newReleasedThisWeek: 3,
  offlineThisWeek: 1,
  monthlyReleasedCount: 11,
  scheduledRunCount: 1180, // 周期跑(定时触发)次数;目标指标,需触发来源字段,现 mock
  dau: 312,
  wau: 1040,
  prevDau: 289,
  sceneCoverage: 9,
  estimatedPersonDays: 86,
  avgDurationMs: 14_000,
  totalTokenUsage: 12_400_000,
  onlineRunsWindow: 1820, // 线上 run(窗口内)
  onlineSuccessRate: 0.964,
  prevOnlineSuccessRate: 0.952,
  successRate: 0.948, // 含测试的总成功率(被测试拉低)
  prevSuccessRate: 0.937,
  // 三主线(H2 焦点,锚定 spec §6)
  completionSuccessRate: 0.964,
  prevCompletionSuccessRate: 0.952,
  selfHealTriggeredRuns: 142,
  selfHealSuccessRate: 0.88,
  prevSelfHealSuccessRate: 0.85,
  machineDurationP50: 12_000,
  prevMachineDurationP50: 13_500,
}

export function mockOverview(from: number, to: number): IDashboardOverview {
  const days = daysBetween(from, to)
  const online = Math.round(BASE.onlineRunsWindow * (days / 7))
  const testRuns = Math.round(online * 0.45)
  const total = online + testRuns
  const nonTerminal = Math.min(11, total)
  const terminal = total - nonTerminal
  const succeeded = Math.round(terminal * BASE.successRate)
  const failed = terminal - succeeded
  return {
    workflowCount: BASE.workflowCount,
    totalRuns: total,
    succeededCount: succeeded,
    failedCount: failed,
    runningCount: 8,
    terminalCount: terminal,
    nonTerminalCount: nonTerminal,
    successRate: BASE.successRate,
    prevSuccessRate: BASE.prevSuccessRate,
    avgDurationMs: BASE.avgDurationMs,
    totalTokenUsage: BASE.totalTokenUsage,
    statusDistribution: {
      succeeded,
      failed: Math.round(failed * 0.7),
      aborted: failed - Math.round(failed * 0.7),
      running: 8,
      waiting: 3,
    },
    releasedWorkflowCount: BASE.releasedWorkflowCount,
    newReleasedThisWeek: BASE.newReleasedThisWeek,
    offlineThisWeek: BASE.offlineThisWeek,
    monthlyReleasedCount: BASE.monthlyReleasedCount,
    // 所选周期内上线数:按窗口天数近似(1 天≈1,7 天≈3,30 天≈11)
    windowReleasedCount: days <= 1 ? 1 : days <= 7 ? BASE.newReleasedThisWeek : BASE.monthlyReleasedCount,
    scheduledRunCount: BASE.scheduledRunCount,
    onlineRuns: online,
    testRuns,
    onlineSuccessRate: BASE.onlineSuccessRate,
    prevOnlineSuccessRate: BASE.prevOnlineSuccessRate,
    onlineStatusDistribution: {
      succeeded: Math.round(online * 0.964),
      failed: Math.round(online * 0.026),
      aborted: Math.round(online * 0.01),
    },
    sceneCoverage: BASE.sceneCoverage,
    estimatedPersonDays: BASE.estimatedPersonDays,
    dau: BASE.dau,
    wau: BASE.wau,
    prevDau: BASE.prevDau,
    completionSuccessRate: BASE.completionSuccessRate,
    prevCompletionSuccessRate: BASE.prevCompletionSuccessRate,
    selfHealTriggeredRuns: BASE.selfHealTriggeredRuns,
    selfHealSuccessRate: BASE.selfHealSuccessRate,
    prevSelfHealSuccessRate: BASE.prevSelfHealSuccessRate,
    selfHealRecoveredRuns: Math.round(BASE.selfHealTriggeredRuns * BASE.selfHealSuccessRate),
    machineDurationP50: BASE.machineDurationP50,
    machineDurationP95: Math.round(BASE.machineDurationP50 * 1.8),
    prevMachineDurationP50: BASE.prevMachineDurationP50,
    durationSampleCount: succeeded,
    evolution: {
      available: true,
      verificationAvailable: true,
      diagnosisCount: 18,
      diagnosedRunCount: 11,
      issueClusterCount: 6,
      suggestionCount: 9,
      applicationAttemptCount: 7,
      applicationSucceededCount: 6,
      applicationFailedCount: 1,
      applicationSuccessRate: 6 / 7,
      appliedUnverifiedCount: 3,
      recurrenceDetectedCount: 1,
      verifiedCount: 4,
    },
  }
}

export function mockDailyTrend(from: number, to: number): IDashboardDailyTrend {
  const days = Math.min(60, daysBetween(from, to))
  const dates: IDailyTrendPoint[] = []
  for (let i = days - 1; i >= 0; i--) {
    const k = days - 1 - i
    const onlineRuns = Math.round(60 + wave(k, 1) * 18 + (k / days) * 8)
    const testRuns = Math.round(28 + wave(k, 4) * 10)
    const onlineRate = clamp(0.945 + wave(k, 2) * 0.025, 0.9, 0.99)
    const totalRate = clamp(onlineRate - 0.03 + wave(k, 3) * 0.01, 0.85, 0.99)
    const total = onlineRuns + testRuns
    const succeeded = Math.round(total * totalRate)
    // 三主线趋势:随 k(时间推进)越跑越顺 —— 完成率↑、自愈成功率↑、机器耗时↓
    const progress = days > 0 ? k / days : 0
    const completionTrend = clamp(0.928 + progress * 0.04 + wave(k, 7) * 0.012, 0.85, 0.99)
    const selfHealTrend = clamp(0.795 + progress * 0.085 + wave(k, 8) * 0.02, 0.7, 0.95)
    const machineTrend = Math.round(14_600 - progress * 3_000 + wave(k, 9) * 700)
    dates.push({
      date: dateLabel(i),
      runCount: total,
      succeededCount: succeeded,
      failedCount: total - succeeded,
      successRate: round(totalRate, 4),
      onlineRuns,
      testRuns,
      onlineSuccessRate: round(onlineRate, 4),
      avgDurationMs: Math.round(13_000 + wave(k, 5) * 1800 + (i % 3) * 400),
      tokenUsage: Math.round(380_000 + wave(k, 6) * 60_000),
      completionSuccessRate: round(completionTrend, 4),
      selfHealSuccessRate: round(selfHealTrend, 4),
      selfHealTriggeredRuns: Math.round(3 + progress * 3 + wave(k, 10) * 1.5),
      machineDurationP50: machineTrend,
    })
  }
  return { from, to, dates }
}

export function mockDurationDistribution(from: number, to: number): IDashboardDurationDistribution {
  const buckets = [
    { label: '<1s', minMs: 0, maxMs: 1000, count: 240 },
    { label: '1-5s', minMs: 1000, maxMs: 5_000, count: 510 },
    { label: '5-30s', minMs: 5_000, maxMs: 30_000, count: 760 },
    { label: '30s-5m', minMs: 30_000, maxMs: 300_000, count: 280 },
    { label: '>5m', minMs: 300_000, maxMs: null, count: 30 },
  ]
  const total = buckets.reduce((s, b) => s + b.count, 0)
  return {
    from,
    to,
    buckets: buckets.map((b) => ({ ...b, percentage: round((b.count / total) * 100, 1) })),
  }
}

export function mockTopWorkflows(from: number, to: number, limit = 10): IDashboardTopWorkflows {
  const rows: Array<Partial<typeof topSeed[number]> & { workflowId: string; workflowTitle: string }> = topSeed.map((r) => ({ ...r }))
  return {
    from,
    to,
    workflows: rows
      .sort((a, b) => (b.runCount ?? 0) - (a.runCount ?? 0))
      .slice(0, limit)
      .map((r) => ({
        workflowId: r.workflowId!,
        workflowTitle: r.workflowTitle!,
        runCount: r.runCount!,
        succeededCount: r.succeededCount!,
        failedCount: r.failedCount!,
        successRate: r.successRate!,
        avgDurationMs: r.avgDurationMs!,
        released: r.released,
        sceneName: r.sceneName,
        onlineRuns: r.onlineRuns,
        onlineSuccessRate: r.onlineSuccessRate,
      })),
  }
}

const topSeed = [
  { workflowId: 'risk-review-pipeline', workflowTitle: '风险评审流水线', runCount: 420, succeededCount: 412, failedCount: 8, successRate: 0.981, avgDurationMs: 18_400, released: true, sceneName: '风控评审', onlineRuns: 380, onlineSuccessRate: 0.984 },
  { workflowId: 'approval-flow', workflowTitle: '审批流', runCount: 310, succeededCount: 295, failedCount: 15, successRate: 0.952, avgDurationMs: 4_200, released: true, sceneName: '审批', onlineRuns: 290, onlineSuccessRate: 0.955 },
  { workflowId: 'marketing-flow-dispatch', workflowTitle: '营销调度', runCount: 268, succeededCount: 259, failedCount: 9, successRate: 0.966, avgDurationMs: 9_800, released: true, sceneName: '营销调度', onlineRuns: 240, onlineSuccessRate: 0.971 },
  { workflowId: 'teamclaw-kf-support', workflowTitle: 'KF 知答', runCount: 210, succeededCount: 198, failedCount: 12, successRate: 0.943, avgDurationMs: 22_000, released: true, sceneName: '客服知识', onlineRuns: 180, onlineSuccessRate: 0.944 },
  { workflowId: 'camp-pingshen-2604-assessment', workflowTitle: '营评审 2604', runCount: 156, succeededCount: 142, failedCount: 14, successRate: 0.91, avgDurationMs: 31_000, released: false, sceneName: '营评审', onlineRuns: 0, onlineSuccessRate: null },
  { workflowId: 'cct-sop-alert', workflowTitle: 'SOP 告警', runCount: 132, succeededCount: 128, failedCount: 4, successRate: 0.97, avgDurationMs: 3_500, released: true, sceneName: '运维告警', onlineRuns: 120, onlineSuccessRate: 0.975 },
  { workflowId: 'approval-web', workflowTitle: '审批 Web', runCount: 98, succeededCount: 91, failedCount: 7, successRate: 0.929, avgDurationMs: 6_100, released: false, sceneName: '审批', onlineRuns: 0, onlineSuccessRate: null },
  { workflowId: 'tech-1-explainer', workflowTitle: '技术解读', runCount: 76, succeededCount: 73, failedCount: 3, successRate: 0.961, avgDurationMs: 12_500, released: true, sceneName: '技术资讯', onlineRuns: 64, onlineSuccessRate: 0.966 },
  { workflowId: 'riskreview-deep', workflowTitle: '深度风险复核', runCount: 64, succeededCount: 58, failedCount: 6, successRate: 0.906, avgDurationMs: 44_000, released: true, sceneName: '风控评审', onlineRuns: 56, onlineSuccessRate: 0.911 },
  { workflowId: 'marketing-flow-dispatch-alert', workflowTitle: '营销告警', runCount: 52, succeededCount: 49, failedCount: 3, successRate: 0.942, avgDurationMs: 2_900, released: true, sceneName: '营销调度', onlineRuns: 46, onlineSuccessRate: 0.956 },
  { workflowId: 'sandbox-exp', workflowTitle: '沙盒实验', runCount: 38, succeededCount: 30, failedCount: 8, successRate: 0.789, avgDurationMs: 15_000, released: false, sceneName: '实验', onlineRuns: 0, onlineSuccessRate: null },
  { workflowId: 'data-repair-dag', workflowTitle: '数据修复', runCount: 29, succeededCount: 27, failedCount: 2, successRate: 0.931, avgDurationMs: 8_700, released: true, sceneName: '数据治理', onlineRuns: 24, onlineSuccessRate: 0.958 },
]

export function mockSubsystemSummary(): IDashboardSubsystemSummary {
  return {
    approval: { pending: 4, approved: 120, rejected: 6, other: 2 },
    alerts: { unacknowledged: 2, critical: 1, warning: 5, info: 18 },
    scheduler: { enabled: 14, disabled: 3 },
    flowControl: { activeSlots: 3, queuedItems: 7 },
  }
}

// ── 工作流发布情况(研发效能)──
// 窗口口径:deployCount/rollbackCount/批均成功率 随 from~to 缩放(模拟 90 天全量按比例);
// 全周期口径:devCycleMs/lastDeployAt/released 不随窗口变。未发布的(released:false)恒 0。
export function mockWorkflowReleaseStats(from: number, to: number): IWorkflowReleaseStats {
  const nowSec = Math.floor(Date.now() / 1000)
  const winDays = Math.max(1, Math.round((to - from) / 86400))
  const scale = Math.min(1, winDays / 90)
  const workflows: IWorkflowReleaseStatRow[] = topSeed.map((r, i) => {
    const created = nowSec - (40 + i * 6) * 86400
    if (!r.released) {
      return {
        workflowId: r.workflowId,
        workflowTitle: r.workflowTitle,
        createdAt: created,
        released: false,
        deployCount: 0,
        rollbackCount: 0,
        devCycleMs: null,
        lastDeployAt: null,
        firstRunSuccessRate: null,
      }
    }
    const h = hashStr(r.workflowId)
    const deployCount = Math.round((2 + (h % 8)) * scale)
    const rollbackCount = h % 9 === 0 && deployCount > 2 ? 1 : 0
    const devCycleMs = (6 + (h % 60)) * 3_600_000 + (h % 10) * 86_400_000 // 6h~2.7d
    const lastDeployAt = nowSec - ((h % 12) + 1) * 86400
    return {
      workflowId: r.workflowId,
      workflowTitle: r.workflowTitle,
      createdAt: created,
      released: true,
      deployCount,
      rollbackCount,
      devCycleMs,
      lastDeployAt,
      firstRunSuccessRate: deployCount > 0 ? (r.onlineSuccessRate ?? null) : null,
    }
  })
  workflows.sort((a, b) => b.deployCount - a.deployCount)
  return { available: true, workflows }
}

export function mockSceneBreakdown(from: number, to: number): IDashboardSceneBreakdown {
  const scenes = sceneSeed.map((s) => {
    const successRate = round(s.succeeded / s.runs, 4)
    return {
      sceneId: s.id,
      sceneName: s.name,
      runCount: s.runs,
      succeededCount: s.succeeded,
      failedCount: s.runs - s.succeeded,
      successRate,
      onlineRuns: s.onlineRuns,
      onlineSuccessRate: round(s.onlineSucceeded / s.onlineRuns, 4),
      releasedWorkflowCount: s.released,
      workflowCount: s.total,
      estimatedPersonDays: s.personDays,
    }
  })
  return { from, to, scenes }
}

const sceneSeed = [
  { id: 'risk-review', name: '风控评审', runs: 484, succeeded: 470, onlineRuns: 436, onlineSucceeded: 427, released: 8, total: 9, personDays: 34 },
  { id: 'approval', name: '审批', runs: 408, succeeded: 386, onlineRuns: 380, onlineSucceeded: 365, released: 6, total: 8, personDays: 18 },
  { id: 'marketing', name: '营销调度', runs: 320, succeeded: 308, onlineRuns: 286, onlineSucceeded: 277, released: 5, total: 6, personDays: 12 },
  { id: 'kf', name: '客服知识', runs: 210, succeeded: 198, onlineRuns: 180, onlineSucceeded: 170, released: 4, total: 5, personDays: 8 },
  { id: 'ops', name: '运维告警', runs: 132, succeeded: 128, onlineRuns: 120, onlineSucceeded: 117, released: 5, total: 5, personDays: 6 },
  { id: 'tech', name: '技术资讯', runs: 76, succeeded: 73, onlineRuns: 64, onlineSucceeded: 62, released: 3, total: 4, personDays: 4 },
  { id: 'data', name: '数据治理', runs: 74, succeeded: 69, onlineRuns: 60, onlineSucceeded: 58, released: 4, total: 5, personDays: 3 },
  { id: 'camp', name: '营评审', runs: 156, succeeded: 142, onlineRuns: 0, onlineSucceeded: 0, released: 0, total: 7, personDays: 1 },
  { id: 'exp', name: '实验', runs: 38, succeeded: 30, onlineRuns: 0, onlineSucceeded: 0, released: 0, total: 5, personDays: 0 },
]

export function mockReleaseEfficiency(from: number, to: number): IDashboardReleaseEfficiency {
  const releases = releaseSeed
  return {
    from,
    to,
    releaseCount: releases.length,
    rollbackCount: releases.filter((r) => r.rolledBack).length,
    rollbackRate: round(releases.filter((r) => r.rolledBack).length / releases.length, 4),
    successRate: round(releases.filter((r) => (r.firstRunSuccessCount / r.firstRuns) >= 0.9).length / releases.length, 4),
    avgReleaseDurationMs: Math.round(releases.reduce((s, r) => s + r.releaseDurationMs, 0) / releases.length),
    releases,
  }
}

const releaseSeed = [
  { workflowId: 'risk-review-pipeline', workflowTitle: '风险评审流水线', sceneName: '风控评审', releasedAt: 1, releaseDurationMs: 42_000, firstRuns: 10, firstRunSuccessCount: 10, rolledBack: false },
  { workflowId: 'approval-flow', workflowTitle: '审批流', sceneName: '审批', releasedAt: 2, releaseDurationMs: 28_000, firstRuns: 10, firstRunSuccessCount: 10, rolledBack: false },
  { workflowId: 'marketing-flow-dispatch', workflowTitle: '营销调度', sceneName: '营销调度', releasedAt: 3, releaseDurationMs: 36_000, firstRuns: 10, firstRunSuccessCount: 9, rolledBack: false },
  { workflowId: 'cct-sop-alert', workflowTitle: 'SOP 告警', sceneName: '运维告警', releasedAt: 4, releaseDurationMs: 19_000, firstRuns: 10, firstRunSuccessCount: 10, rolledBack: false },
  { workflowId: 'data-repair-dag', workflowTitle: '数据修复', sceneName: '数据治理', releasedAt: 5, releaseDurationMs: 31_000, firstRuns: 10, firstRunSuccessCount: 10, rolledBack: false },
].map((r) => ({
  ...r,
  firstRunSuccessRate: round(r.firstRunSuccessCount / r.firstRuns, 4),
})) as Array<import('../../types/dashboard').IReleaseEvent>

export function mockFailureHotspots(from: number, to: number): IDashboardFailureHotspots {
  const total = 100
  const hotspots = [
    { nodeLabel: 'fetch-data', workflowTitle: '风险评审流水线', sceneName: '风控评审', count: 60, exampleError: 'downstream API timeout (4s > 限流阈值)', ticketRef: '#123' },
    { nodeLabel: 'llm-evaluate', workflowTitle: 'KF 知答', sceneName: '客服知识', count: 18, exampleError: 'output contract schema mismatch: missing field "answer"' },
    { nodeLabel: 'approval-wait', workflowTitle: '审批流', sceneName: '审批', count: 9, exampleError: 'human approval timed out (>30m)' },
    { nodeLabel: 'mcp-call:kb-search', workflowTitle: '营评审 2604', sceneName: '营评审', count: 7, exampleError: 'mcp tool kb-search returned empty for query' },
    { nodeLabel: 'pipeline-step', workflowTitle: '深度风险复核', sceneName: '风控评审', count: 6, exampleError: 'json parse error on step3 output' },
  ]
  return {
    from,
    to,
    hotspots: hotspots.map((h) => ({ ...h, sharePct: round((h.count / total) * 100, 1) })),
  }
}
// ── L2/L3 用 ──
function hashStr(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

const SCENE_OF: Record<string, string> = {
  'risk-review-pipeline': '风控评审', 'approval-flow': '审批', 'marketing-flow-dispatch': '营销调度',
  'teamclaw-kf-support': '客服知识', 'camp-pingshen-2604-assessment': '营评审', 'cct-sop-alert': '运维告警',
  'approval-web': '审批', 'tech-1-explainer': '技术资讯', 'riskreview-deep': '风控评审',
  'marketing-flow-dispatch-alert': '营销调度', 'sandbox-exp': '实验', 'data-repair-dag': '数据治理',
}

export function mockWorkflowHealth(from: number, to: number): IWorkflowHealthResponse {
  const rows: IWorkflowHealthRow[] = topSeed.map((r) => {
    const h = hashStr(r.workflowId)
    const completion = clamp(round((r.successRate ?? 0.9) + (h % 7) / 100 - 0.03, 4), 0.7, 0.99)
    const healRuns = Math.round((r.runCount ?? 0) * (0.05 + (h % 5) / 100))
    const healRate = clamp(round(0.78 + (h % 12) / 100, 4), 0.6, 0.97)
    const machine = Math.round(6000 + (h % 40) * 600)
    return {
      workflowId: r.workflowId!,
      workflowTitle: r.workflowTitle!,
      sceneName: SCENE_OF[r.workflowId!] ?? r.sceneName ?? '其他',
      released: r.released ?? false,
      runCount: r.runCount ?? 0,
      completionSuccessRate: completion,
      selfHealTriggeredRuns: healRuns,
      selfHealSuccessRate: healRate,
      machineDurationP50: machine,
      avgDurationMs: r.avgDurationMs ?? machine,
    }
  })
  return { from, to, workflows: rows }
}

const NODE_TEMPLATES = [
  { id: 'fetch-data', title: '取数', executorType: 'cli-script' },
  { id: 'llm-evaluate', title: 'LLM 评估', executorType: 'embedded-agent' },
  { id: 'approval-wait', title: '人工审批', executorType: 'approval-card-web' },
  { id: 'mcp-call-kb', title: 'KB 检索', executorType: 'mcp-call' },
  { id: 'pipeline-step', title: '处理步骤', executorType: 'cli-script' },
  { id: 'aggregate', title: '汇总', executorType: 'cli-script' },
]

export function mockWorkflowMetrics(workflowId: string, from: number, to: number): IWorkflowMetricsResponse {
  const seed = topSeed.find((r) => r.workflowId === workflowId) ?? topSeed[0]
  const h = hashStr(workflowId)
  const days = Math.min(60, Math.max(1, Math.round((to - from) / 86400)))
  const baseCompletion = clamp(round((seed.successRate ?? 0.9) + (h % 7) / 100 - 0.03, 4), 0.7, 0.99)
  const baseHeal = clamp(round(0.78 + (h % 12) / 100, 4), 0.6, 0.97)
  const baseMachine = Math.round(6000 + (h % 40) * 600)
  const trend: IDailyTrendPoint[] = []
  for (let i = days - 1; i >= 0; i--) {
    const k = days - 1 - i
    const progress = days > 0 ? k / days : 0
    const completionTrend = clamp(baseCompletion + (progress - 0.5) * 0.05 + wave(k, h % 9) * 0.015, 0.6, 0.99)
    const healTrend = clamp(baseHeal + (progress - 0.5) * 0.08 + wave(k, h % 7) * 0.02, 0.5, 0.97)
    const machineTrend = Math.round(baseMachine - (progress - 0.5) * 2000 + wave(k, h % 5) * 600)
    const runs = Math.round(6 + wave(k, h % 3) * 3)
    trend.push({
      date: dateLabel(i),
      runCount: runs,
      succeededCount: Math.round(runs * completionTrend),
      failedCount: Math.round(runs * (1 - completionTrend)),
      successRate: round(completionTrend, 4),
      onlineSuccessRate: round(completionTrend, 4),
      onlineRuns: runs,
      testRuns: 0,
      avgDurationMs: machineTrend,
      tokenUsage: 0,
      completionSuccessRate: round(completionTrend, 4),
      selfHealSuccessRate: round(healTrend, 4),
      selfHealTriggeredRuns: Math.round(runs * 0.2),
      machineDurationP50: machineTrend,
    })
  }
  const nodeCount = 4 + (h % 3)
  const nodeHealth: IWorkflowNodeHealthRow[] = []
  for (let n = 0; n < nodeCount; n++) {
    const tpl = NODE_TEMPLATES[n % NODE_TEMPLATES.length]
    const nh = hashStr(workflowId + n)
    const rc = Math.round((seed.runCount ?? 0) * (0.4 - n * 0.06)) + (n === 0 ? 1 : 0)
    const rate = clamp(round(0.85 + ((nh % 14) / 100) - 0.02, 4), 0.5, 0.99)
    const isApproval = tpl.executorType.startsWith('approval')
    const retryCount = Math.round(rc * (isApproval ? 0 : 0.08 + (nh % 5) / 100))
    const retrySucceededCount = Math.round(retryCount * (0.6 + (nh % 4) / 10))
    nodeHealth.push({
      nodeId: tpl.id + (n >= NODE_TEMPLATES.length ? `_${Math.floor(n / NODE_TEMPLATES.length)}` : ''),
      nodeTitle: tpl.title,
      executorType: tpl.executorType,
      runCount: rc,
      succeededCount: Math.round(rc * rate),
      successRate: rate,
      retryCount,
      retrySucceededCount,
      healSuccessRate: retryCount > 0 ? round(retrySucceededCount / retryCount, 4) : null,
      avgDurationMs: isApproval ? 180_000 : Math.round(3000 + (nh % 30) * 400),
      topError: rate < 0.9 && !isApproval ? (n === 0 ? 'downstream API timeout' : 'output contract schema mismatch') : undefined,
    })
  }
  const failureHotspots = nodeHealth
    .filter((n) => n.topError)
    .map((n, idx) => {
      const count = Math.round((1 - (n.successRate ?? 1)) * n.runCount * 2) + 1
      return {
        nodeLabel: n.nodeId,
        workflowTitle: seed.workflowTitle!,
        sceneName: SCENE_OF[workflowId] ?? '其他',
        count,
        sharePct: round((count / (n.runCount || 1)) * 100, 1),
        exampleError: n.topError!,
        ticketRef: idx === 0 && count > 5 ? '#123' : undefined,
      }
    })
    .sort((a, b) => b.count - a.count)
  return {
    workflowId,
    workflowTitle: seed.workflowTitle!,
    sceneName: SCENE_OF[workflowId] ?? '其他',
    released: seed.released ?? false,
    runCount: seed.runCount ?? 0,
    completionSuccessRate: baseCompletion,
    selfHealTriggeredRuns: Math.round((seed.runCount ?? 0) * (0.05 + (h % 5) / 100)),
    selfHealSuccessRate: baseHeal,
    machineDurationP50: baseMachine,
    trend,
    nodeHealth,
    failureHotspots,
  }
}

// ── 单指标趋势(KPI 点钻弹窗)──
// 按 granularity 把窗口切成若干桶,值用确定性 wave 生成;每指标基线/量纲不同。
export function mockMetricTrend(
  metric: MetricKey,
  granularity: TrendGranularity,
  from: number,
  to: number,
): IDashboardMetricTrend {
  const stepDays = granularity === 'day' ? 1 : granularity === 'week' ? 7 : 30
  const totalDays = Math.max(1, Math.round((to - from) / 86400))
  const n = Math.min(60, Math.max(1, Math.round(totalDays / stepDays)))
  const points: Array<{ bucket: string; value: number | null }> = []
  for (let i = n - 1; i >= 0; i--) {
    const k = n - 1 - i
    const progress = n > 0 ? k / n : 0
    let v: number | null
    switch (metric) {
      case 'runs': v = Math.max(1, Math.round(88 + wave(k, 1) * 24 + progress * 10)); break
      case 'activeWorkflows': v = Math.max(1, Math.round(9 + wave(k, 1) * 2.5 + progress * 1)); break
      case 'dau': v = Math.max(1, Math.round(36 + wave(k, 1) * 10 + progress * 4)); break
      case 'releases': v = Math.max(0, Math.round(1 + wave(k, 3) * 1.6 + (k % 5 === 0 ? 1 : 0) - 0.6)); break
      case 'deploys': v = Math.max(0, Math.round(4 + wave(k, 2) * 2 + (k % 3 === 0 ? 2 : 0))); break
      case 'completionRate': v = round(clamp(0.9 + progress * 0.05 + wave(k, 7) * 0.012, 0.8, 0.99), 4); break
      case 'successRate': v = round(clamp(0.94 + wave(k, 3) * 0.015, 0.85, 0.99), 4); break
      case 'machineP50': v = Math.round(14_600 - progress * 3_000 + wave(k, 9) * 700); break
      case 'releaseSuccessRate': v = round(clamp(0.78 + progress * 0.19 + wave(k, 5) * 0.03, 0.7, 0.99), 4); break
      case 'rollbackRate': v = round(clamp(0.22 - progress * 0.18 + wave(k, 6) * 0.04, 0, 0.4), 4); break
      case 'deliveryLagHours': v = round(clamp(26 - progress * 18 + wave(k, 8) * 4, 1, 48), 1); break
      // 字段未落地指标:demo 给形态(mock),真实后端恒 available:false
      case 'selfHealRate': v = round(clamp(0.78 + progress * 0.1 + wave(k, 11) * 0.02, 0.7, 0.95), 4); break
      case 'onlineCompletionRate': v = round(clamp(0.945 + progress * 0.02 + wave(k, 12) * 0.01, 0.88, 0.99), 4); break
      case 'onlineRuns': v = Math.max(1, Math.round(60 + wave(k, 1) * 18 + progress * 8)); break
      case 'testRuns': v = Math.max(1, Math.round(28 + wave(k, 4) * 10)); break
      default: v = null
    }
    const offsetDays = i * stepDays
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() - offsetDays)
    const bucket =
      granularity === 'day'
        ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
        : granularity === 'week'
          ? `${d.getFullYear()}-W${String(getWeek(d)).padStart(2, '0')}`
          : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    points.push({ bucket, value: v })
  }
  points.sort((a, b) => (a.bucket < b.bucket ? -1 : 1))
  return { available: true, metric, granularity, from, to, points }
}

// ISO 周序号(Monday-based,与后端 SQLite %W / MySQL %u 对齐:0-53)
function getWeek(d: Date): number {
  const t = new Date(d.valueOf())
  t.setHours(0, 0, 0, 0)
  t.setDate(t.getDate() + 3 - ((t.getDay() + 6) % 7))
  const week1 = new Date(t.getFullYear(), 0, 4)
  return 1 + Math.round(((t.getTime() - week1.getTime()) / 86400000 - 3 + ((week1.getDay() + 6) % 7)) / 7)
}

// ── 发布质量趋势(发布效能区块看迭代效果:发布数/成功率/回滚率 per 桶)──
// 故事:成功率随周推进从 ~0.78 爬到 ~0.97,回滚率压到 0~1 次。
export function mockReleaseQualityTrend(
  granularity: TrendGranularity,
  from: number,
  to: number,
): IDashboardReleaseQualityTrend {
  const stepDays = granularity === 'day' ? 1 : granularity === 'week' ? 7 : 30
  const totalDays = Math.max(1, Math.round((to - from) / 86400))
  const n = Math.min(40, Math.max(1, Math.round(totalDays / stepDays)))
  const points: Array<{ bucket: string; releaseCount: number; rollbackCount: number; successRate: number | null; rollbackRate: number | null }> = []
  for (let i = n - 1; i >= 0; i--) {
    const k = n - 1 - i
    const progress = n > 0 ? k / n : 0
    const releaseCount = Math.max(1, Math.round(4 + wave(k, 2) * 2 + (k % 3 === 0 ? 2 : 0)))
    const rollbackCount = k < 2 ? 1 : (k % 6 === 0 ? 1 : 0)
    const successRate = round(clamp(0.78 + progress * 0.19 + wave(k, 5) * 0.03, 0.7, 0.99), 4)
    const rollbackRate = round(releaseCount > 0 ? rollbackCount / releaseCount : 0, 4)
    const offsetDays = i * stepDays
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() - offsetDays)
    const bucket =
      granularity === 'day'
        ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
        : granularity === 'week'
          ? `${d.getFullYear()}-W${String(getWeek(d)).padStart(2, '0')}`
          : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    points.push({ bucket, releaseCount, rollbackCount, successRate, rollbackRate })
  }
  points.sort((a, b) => (a.bucket < b.bucket ? -1 : 1))
  return { available: true, from, to, granularity, points }
}
