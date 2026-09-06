// 核心响应类型
export interface IDashboardOverview {
  workflowCount: number
  totalRuns: number
  succeededCount: number
  failedCount: number
  runningCount: number
  terminalCount: number
  nonTerminalCount: number
  successRate: number | null
  prevSuccessRate: number | null
  avgDurationMs: number | null
  totalTokenUsage: number
  statusDistribution: Record<string, number>

  // ── 目标指标(发布机制/场景能力到位后由后端填充;未到位时缺省,demo 模式由 mock 填)──
  releasedWorkflowCount?: number
  newReleasedThisWeek?: number
  offlineThisWeek?: number
  monthlyReleasedCount?: number          // 本月上线工作流数(研发效能)
  windowReleasedCount?: number           // 页面所选周期内 deploy 过的 distinct 工作流数(跟 timeRange)
  scheduledRunCount?: number             // 周期跑次数(目标指标:需 flow_runs 触发来源字段,未到位时缺省)
  onlineRuns?: number
  testRuns?: number
  onlineSuccessRate?: number | null
  prevOnlineSuccessRate?: number | null
  onlineStatusDistribution?: Record<string, number>
  sceneCoverage?: number
  estimatedPersonDays?: number
  dau?: number
  wau?: number
  prevDau?: number

  // ── 三主线(H2 焦点:长程任务 + looprun)──
  completionSuccessRate?: number | null       // 长程任务完成成功率
  prevCompletionSuccessRate?: number | null
  selfHealTriggeredRuns?: number              // 自愈次数(run 粒度)
  selfHealSuccessRate?: number | null         // 自愈成功率
  prevSelfHealSuccessRate?: number | null
  selfHealRecoveredRuns?: number              // 自愈挽回 run 数(触发数×成功率)
  machineDurationP50?: number | null          // 端到端完成耗时 P50(ms,含人工等待)
  machineDurationP95?: number | null
  prevMachineDurationP50?: number | null
  durationSampleCount?: number

  evolution?: {
    available: boolean
    verificationAvailable?: boolean
    diagnosisCount: number
    diagnosedRunCount?: number
    issueClusterCount?: number
    suggestionCount: number
    applicationAttemptCount: number
    applicationSucceededCount: number
    applicationFailedCount: number
    applicationSuccessRate: number | null
    appliedUnverifiedCount: number | null
    recurrenceDetectedCount: number | null
    verifiedCount: number | null
  }
}

export interface IDailyTrendPoint {
  date: string
  runCount: number
  succeededCount: number
  failedCount: number
  successRate: number | null
  avgDurationMs: number | null
  tokenUsage: number

  // 目标指标:线上/测试分轨
  onlineRuns?: number
  testRuns?: number
  onlineSuccessRate?: number | null

  // 三主线趋势
  completionSuccessRate?: number | null
  selfHealSuccessRate?: number | null
  selfHealTriggeredRuns?: number
  machineDurationP50?: number | null
}

export interface IDashboardDailyTrend {
  from: number
  to: number
  dates: IDailyTrendPoint[]
}

export interface IDurationBucket {
  label: string
  minMs: number
  maxMs: number | null
  count: number
  percentage: number
}

export interface IDashboardDurationDistribution {
  from: number
  to: number
  buckets: IDurationBucket[]
}

export interface ITopWorkflow {
  workflowId: string
  workflowTitle: string
  runCount: number
  succeededCount: number
  failedCount: number
  successRate: number | null
  avgDurationMs: number | null

  // 目标指标
  released?: boolean
  sceneName?: string
  onlineRuns?: number
  onlineSuccessRate?: number | null
}

export interface IDashboardTopWorkflows {
  from: number
  to: number
  workflows: ITopWorkflow[]
}

// ── 目标指标:按业务线切片 ──
export interface ISceneBreakdownItem {
  sceneId: string
  sceneName: string
  runCount: number
  succeededCount: number
  failedCount: number
  successRate: number | null
  onlineRuns: number
  onlineSuccessRate: number | null
  releasedWorkflowCount: number
  workflowCount: number
  estimatedPersonDays: number
}

export interface IDashboardSceneBreakdown {
  available?: boolean               // false = business_scenes 表未建,暂不可算
  from: number
  to: number
  scenes: ISceneBreakdownItem[]
}

// ── 目标指标:发布效能 ──
export interface IReleaseEvent {
  workflowId: string
  workflowTitle: string
  sceneName: string
  releasedAt: number
  releaseDurationMs: number
  firstRuns: number
  firstRunSuccessCount: number
  firstRunSuccessRate: number | null
  rolledBack: boolean
}

export interface IDashboardReleaseEfficiency {
  available?: boolean              // false = 当前库不可算(SQLite 无 workflow_deploy_history),前端占位
  from: number
  to: number
  releaseCount: number
  rollbackCount: number
  rollbackRate: number | null
  successRate: number | null
  avgReleaseDurationMs: number | null
  releases: IReleaseEvent[]
}

// ── 目标指标:失败归因 ──
export interface IFailureHotspot {
  nodeLabel: string
  workflowTitle: string
  sceneName: string
  count: number
  sharePct: number
  exampleError: string
  ticketRef?: string
}

export interface IDashboardFailureHotspots {
  from: number
  to: number
  hotspots: IFailureHotspot[]
}

// ── 单指标趋势(KPI 点钻)──
export type MetricKey =
  | 'runs' | 'activeWorkflows' | 'dau' | 'releases'
  | 'completionRate' | 'successRate' | 'machineP50'
  | 'deploys' | 'releaseSuccessRate' | 'rollbackRate' | 'deliveryLagHours'
  // 字段未落地,后端恒 available:false;demo 有 mock 趋势
  | 'selfHealRate' | 'onlineCompletionRate' | 'onlineRuns' | 'testRuns'
export type TrendGranularity = 'day' | 'week' | 'month'

export interface IDashboardMetricTrend {
  available: boolean               // false = 该指标当前库不可算(如 release 系在 SQLite)
  metric: MetricKey
  granularity: TrendGranularity
  from: number
  to: number
  points: Array<{ bucket: string; value: number | null }>   // null = 该桶无样本,图上断点
}

// ── 发布质量趋势(发布效能区块看迭代效果)──
export interface IReleaseQualityPoint {
  bucket: string
  releaseCount: number
  rollbackCount: number
  successRate: number | null
  rollbackRate: number | null
}

export interface IDashboardReleaseQualityTrend {
  available: boolean               // false = SQLite 无 workflow_deploy_history
  from: number
  to: number
  granularity: TrendGranularity
  points: IReleaseQualityPoint[]
}

// ── 工作流发布情况(研发效能,全量资产视角,MySQL only)──
export interface IWorkflowReleaseStatRow {
  workflowId: string
  workflowTitle: string
  createdAt: number | null
  released: boolean                  // deployCount > 0
  deployCount: number
  rollbackCount: number
  devCycleMs: number | null          // 研发周期 = 首次部署 − 首次保存(含排期等待,参考量)
  lastDeployAt: number | null
  firstRunSuccessRate: number | null
}

export interface IWorkflowReleaseStats {
  available: boolean                 // false = SQLite 无 workflow_deploy_history
  workflows: IWorkflowReleaseStatRow[]
}

export interface ISubsystemSummary {
  approval: { pending: number; approved: number; rejected: number; other: number }
  alerts: { unacknowledged: number; critical: number; warning: number; info: number }
  scheduler: { enabled: number; disabled: number }
  flowControl: { activeSlots: number; queuedItems: number }
}

export type IDashboardSubsystemSummary = ISubsystemSummary

export type TimeRangeKey = 'today' | '7d' | '30d'

export interface DrilldownFilter {
  title: string
  status?: string
  workflowId?: string
  minDurationMs?: number
  maxDurationMs?: number
  dateFrom?: number
  dateTo?: number
}

// ── L2:工作流健康表(全量,三主线 per workflow)──
export interface IWorkflowHealthRow {
  workflowId: string
  workflowTitle: string
  sceneName: string
  released: boolean
  runCount: number
  completionSuccessRate: number | null
  selfHealTriggeredRuns: number
  selfHealSuccessRate: number | null
  machineDurationP50: number | null
  avgDurationMs: number | null
}

export interface IWorkflowHealthResponse {
  available?: boolean                // false = L2 暂未真实化(stub 占位)
  from: number
  to: number
  workflows: IWorkflowHealthRow[]
}

// ── L3:单工作流详情 ──
export interface IWorkflowNodeHealthRow {
  nodeId: string
  nodeTitle: string
  executorType: string
  runCount: number
  succeededCount: number
  successRate: number | null
  retryCount: number
  retrySucceededCount: number
  healSuccessRate: number | null   // 该节点重试(自愈)后最终成功的比例
  avgDurationMs: number | null
  topError?: string
}

export interface IWorkflowMetricsResponse {
  available?: boolean                // false = L3 暂未真实化(stub 占位)
  workflowId: string
  workflowTitle: string
  sceneName: string
  released: boolean
  runCount: number
  completionSuccessRate: number | null
  selfHealTriggeredRuns: number
  selfHealSuccessRate: number | null
  machineDurationP50: number | null
  trend: IDailyTrendPoint[]
  nodeHealth: IWorkflowNodeHealthRow[]
  failureHotspots: IFailureHotspot[]
}
