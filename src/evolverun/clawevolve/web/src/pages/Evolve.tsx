import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type EvolveStep, type EvolveTask, type EvolveTaskLogArchive, type EvolveVersion } from '../api/client'
import { insightApi } from '../api/insight'
import { useClientUser } from '../hooks/useClientUser'
import type { BenchDomain, TCLogBot } from '../types'
import type { ImprovementHandoff, ImprovementView } from '../types/insight'
import { createRequestId } from './InsightCenter/utils'
import BenchDomains from './BenchDomains'
import BenchTemplateDetail from './BenchTemplateDetail'
import BenchRunDetail from './BenchRunDetail'
import SessionAnalysis from './SessionAnalysis'
import Repair from './Repair'
import TCLog from './TCLog'
import { evolveTaskRegistry, isEvolveTaskType } from '../features/evolve/task-registry'
import EvolveBotPicker from '../components/EvolveBotPicker'
import { evolveBotOptionKey } from '../components/evolveBotIdentity'
import EvolveModelFields, { EVOLVE_CUSTOM_MODEL, EVOLVE_MODEL_OPTIONS } from '../components/EvolveModelFields'
import EvolveTaskOverview from '../components/EvolveTaskOverview'
import { EvolveAdminScopeProvider, useEvolveAdminScope } from '../features/evolve/admin-scope'
import {

  governanceImprovementId,
  isGovernanceTask,
  taskDisplayType,
} from '../features/evolve/task-presentation'
import { GitDiffView, TaskType } from './evolve/common'
import { TaskList } from './evolve/TaskList'
import { PackManagement } from './evolve/PackManagement'

type IconName = 'spark' | 'plus' | 'bot' | 'arrow' | 'check' | 'clock' | 'file' | 'chart' | 'code' | 'send' | 'target' | 'package'

function Icon({ name, className = 'h-4 w-4' }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    spark: <><path d="m12 3-1.2 3.8a6 6 0 0 1-4 4L3 12l3.8 1.2a6 6 0 0 1 4 4L12 21l1.2-3.8a6 6 0 0 1 4-4L21 12l-3.8-1.2a6 6 0 0 1-4-4L12 3Z" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    bot: <><rect x="4" y="7" width="16" height="12" rx="3" /><path d="M9 12h.01M15 12h.01M8 16h8M12 3v4" /></>,
    arrow: <path d="m9 18 6-6-6-6" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    file: <><path d="M6 3h8l4 4v14H6V3Z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></>,
    chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19H2" /></>,
    code: <><path d="m8 9-3 3 3 3M16 9l3 3-3 3M14 5l-4 14" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="M22 2 11 13" /></>,
    target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>,
    package: <><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" /><path d="m4.5 7.5 7.5 4 7.5-4M12 11.5V21" /></>,
  }
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700'
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50'
const inputClass = 'w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10'
type FullInputMode = 'diagnose_goal' | 'direct_goal'

function packRestoreTaskName(pack: {
  botId: string
  sourceKind: 'baseline' | 'snapshot' | 'round'
  sourceRound?: number | null
}) {
  const version = pack.sourceKind === 'baseline'
    ? '初始版本'
    : pack.sourceKind === 'round' && pack.sourceRound
      ? `第 ${pack.sourceRound} 轮版本`
      : '快照版本'
  return `${pack.sourceKind === 'baseline' ? '恢复' : '应用'} ${pack.botId} · ${version}`
}
const nodeYaml = (command: string) => `version: "1.0"\ncommand: >-\n  ${command}\n`
type NodeDefinition = { key: string; label: string; defaultCommand: string }

function RuntimeMaintenanceOption({ enabled, onChange }: { enabled: boolean; onChange: (enabled: boolean) => void }) {
  return (
    <section className="rounded-xl border border-amber-200 bg-amber-50/80 p-4">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-100 text-sm font-semibold text-amber-700">!</span>
        <div>
          <p className="text-sm font-semibold text-amber-950">运行前维护</p>
          <label className="mt-3 flex cursor-pointer items-start gap-2 text-xs font-medium text-amber-950">
            <input type="checkbox" checked={enabled} onChange={(event) => onChange(event.target.checked)} className="mt-0.5 h-4 w-4 rounded border-amber-400 text-amber-600" />
            <span>任务启动前清理 ClawEvolve 历史会话并重启 Gateway（推荐）</span>
          </label>
          <p className="mt-2 text-xs leading-5 text-amber-800">{enabled
            ? '维护过程会临时调整 openclaw.json：关闭 AgentGuard，并调整 ClawMind 的 API、流控和上下文压缩配置。Gateway 重启会影响该 Bot 当前正在运行的会话和任务，并可能导致这些任务失败；任务完成后如需继续使用原有配置，可在 TC 中使用“恢复配置”一键恢复。'
            : '未清理历史会话且不重启 Gateway，残留运行状态可能导致本次进化任务失败。'}</p>
        </div>
      </div>
    </section>
  )
}

function dateValue(offsetDays = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function buildDiagnoseIntent(input: {
  lookbackDays: number
  startDate: string
  endDate: string
  useDateRange: boolean
  badCaseCount: number
  goodCaseCount: number
  focusIssue: string
}): string {
  const timeRange = input.useDateRange
    ? `${input.startDate} 至 ${input.endDate}`
    : `最近${input.lookbackDays}天`
  const caseRequirement = input.badCaseCount > 0 && input.goodCaseCount > 0
    ? `抽取${input.badCaseCount}个 bad case 和${input.goodCaseCount}个 good case`
    : input.badCaseCount > 0
      ? `抽取${input.badCaseCount}个 bad case，不需要good case`
      : `抽取${input.goodCaseCount}个 good case，不需要bad case`
  return `扫描${timeRange}的历史 session；${caseRequirement}；重点关注${input.focusIssue.trim()}。`
}

function Status({ type, children }: { type: 'running' | 'waiting' | 'done' | 'scheduled'; children: ReactNode }) {
  const style = {
    running: 'bg-blue-50 text-blue-700',
    waiting: 'bg-violet-50 text-violet-700',
    done: 'bg-emerald-50 text-emerald-700',
    scheduled: 'bg-gray-100 text-gray-600',
  }[type]
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${style}`}>{children}</span>
}

const taskTypeText = Object.fromEntries(
  Object.values(evolveTaskRegistry).map((definition) => [definition.type, definition.label]),
) as Record<string, string>
taskTypeText.session_analysis = '会话诊断'
taskTypeText.session_export = 'Session 导出'
type TaskCategory = 'all' | 'diagnosis' | 'optimization' | 'repair' | 'deployment' | 'full'
const taskCategoryText: Record<TaskCategory, string> = {
  all: '全部类型', diagnosis: '诊断', optimization: '优化', repair: '修复', deployment: '部署', full: '全流程最佳实践',
}

const taskStatusText: Record<string, string> = {
  pending: '待运行', dispatched: '运行中', running: '运行中',
  waiting_approval: '等待批准方案', waiting_acceptance: '等待确认结果', waiting_context: '等待补充上下文',
  completed: '已完成', failed: '失败', canceled: '已取消',
}
const taskStepText: Record<string, string> = {
  skill_init: 'Skill 初始化',
  diagnose: 'Bot诊断',
  plan: '目标规划',
  envprep: '环境准备',
  bench: 'Bench诊断',
  optimize: '策略优化',
  apply: '结果应用',
  pack: '创建 Pack',
  pack_restore: '应用 Pack',
  runtime_cleanup: '任务清理',
  session_ais: '会话诊断',
  repair_plan: '生成修复方案',
  repair_apply: '执行与验证',
}

function statusView(status: string): { type: 'running' | 'waiting' | 'done' | 'scheduled'; text: string } {
  if (status === 'completed' || status === 'succeeded') return { type: 'done', text: taskStatusText[status] ?? '已完成' }
  if (status === 'running' || status === 'accepted') return { type: 'running', text: taskStatusText[status] ?? '运行中' }
  if (status === 'dispatched') return { type: 'running', text: '运行中' }
  if (['waiting_approval', 'waiting_acceptance', 'waiting_context'].includes(status)) return { type: 'waiting', text: taskStatusText[status] }
  if (status === 'failed' || status === 'canceled') return { type: 'waiting', text: taskStatusText[status] ?? (status === 'failed' ? '失败' : '已取消') }
  return { type: 'scheduled', text: taskStatusText[status] ?? status }
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}…` : value
}

function improvementTaskName(handoff: ImprovementHandoff): string {
  const suffix = ' · 进化'
  return `${handoff.improvement.title.slice(0, 128 - suffix.length)}${suffix}`
}

function timestampMs(value: number | string | null | undefined): number | null {
  if (value == null) return null
  const timestamp = typeof value === 'number'
    ? (value < 10_000_000_000 ? value * 1000 : value)
    : new Date(value).getTime()
  return Number.isFinite(timestamp) ? timestamp : null
}

function taskLifecycle(task: EvolveTask): {
  currentStep: EvolveStep | null
  startedAt: number | string | null
  completedAt: number | string | null
  duration: string
} {
  const steps = task.steps ?? []
  const currentStep = [...steps].reverse().find((step) =>
    ['created', 'pending', 'accepted', 'dispatched', 'running'].includes(step.status),
  ) ?? steps.at(-1) ?? null
  const starts = steps.flatMap((step) => {
    const value = timestampMs(step.startedAt)
    return step.startedAt != null && value != null ? [{ raw: step.startedAt, value }] : []
  })
  const completions = steps.flatMap((step) => {
    const value = timestampMs(step.completedAt)
    return step.completedAt != null && value != null ? [{ raw: step.completedAt, value }] : []
  })
  const start = starts.length ? starts.reduce((earliest, entry) => entry.value < earliest.value ? entry : earliest) : null
  const terminal = ['completed', 'succeeded', 'failed', 'canceled'].includes(task.status)
  const end = terminal && completions.length
    ? completions.reduce((latest, entry) => entry.value > latest.value ? entry : latest)
    : null
  if (!start) return { currentStep, startedAt: null, completedAt: end?.raw ?? null, duration: '等待启动' }
  const seconds = Math.max(0, Math.floor(((end?.value ?? Date.now()) - start.value) / 1000))
  const duration = seconds < 60
    ? `${seconds} 秒`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)} 分钟`
      : `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`
  return { currentStep, startedAt: start.raw, completedAt: end?.raw ?? null, duration }
}

function taskDetailPath(task: EvolveTask): string {
  if (task.task_type === 'repair') return `/evolve/repair-runs/${task.task_id}`
  return task.task_type === 'session_analysis' || task.task_type === 'session_export'
    ? `/evolve/session-runs/${task.task_id}`
    : `/evolve/runs/${task.task_id}`
}

function improvementRemark(handoff: ImprovementHandoff): string {
  const { improvement } = handoff
  const prefix = `来自 Insight Center 改进项：${improvement.improvementId}\n用户判断与改进方向：`
  const suffix = `\n失败 Task：${improvement.evidenceCount} 个`
  const guidance = improvement.userGuidance?.trim() || '未填写，请结合冻结的失败任务证据继续诊断。'
  const available = Math.max(0, 1000 - prefix.length - suffix.length)
  const visibleGuidance = guidance.length > available
    ? `${guidance.slice(0, Math.max(0, available - 1))}…`
    : guidance
  return `${prefix}${visibleGuidance}${suffix}`
}

function improvementStatusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case 'IN_PROGRESS': return '处理中'
    case 'RESOLVED': return '已完成'
    case 'ARCHIVED': return '已驳回'
    default: return '待处理'
  }
}

function botProviderLabel(provider: TCLogBot['deviceProvider']): string {
  if (provider?.toLowerCase() === 'baas') return 'BaaS Bot'
  if (provider?.toLowerCase() === 'arca') return 'ARCA Bot'
  return '平台未知'
}

function isOpenClawBot(bot: TCLogBot | undefined): boolean {
  return !bot?.activeEngine || bot.activeEngine.toLowerCase() === 'openclaw'
}

function botDispatchPlan(provider: TCLogBot['deviceProvider']): string {
  if (provider?.toLowerCase() === 'baas') return 'BaaS execute-command（失败不自动降级）'
  if (provider?.toLowerCase() === 'arca') return 'Bot Message'
  return 'Bot Message（平台配置未知）'
}

function stepDispatchLabel(step: EvolveStep): string | null {
  const dispatch = step.botResponse?.evolve_dispatch as {
    transport?: string; fallbackFrom?: string
  } | undefined
  if (!dispatch) return null
  if (dispatch.fallbackFrom === 'baas_execute_command') return 'BaaS 命令失败 → Bot Message'
  if (dispatch.transport === 'baas_execute_command') return 'BaaS execute-command'
  if (dispatch.transport === 'message') return 'Bot Message'
  return null
}

function PageTitle({ action }: { action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-gray-950">进化任务</h1>
        <p className="mt-1.5 text-sm text-gray-500">向指定 Bot 发起进化，并查看任务执行结果。</p>
      </div>
      {action}
    </div>
  )
}

function StartEvolution() {
  const navigate = useNavigate()
  const { user, authState } = useClientUser()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialType = searchParams.get('type')
  const taskType = isEvolveTaskType(initialType) && initialType !== 'repair' ? initialType : null
  const improvementSource = searchParams.get('source') === 'improvement'
  const adminConsentToken = searchParams.get('adminConsent')?.trim() ?? ''
  const adminAutoExecute = Boolean(adminConsentToken)
  const rawImprovementId = improvementSource ? (searchParams.get('improvementId')?.trim() ?? '') : ''
  const parsedImprovementId = Number(rawImprovementId)
  const improvementId = /^\d+$/.test(rawImprovementId)
    && Number.isSafeInteger(parsedImprovementId)
    && parsedImprovementId > 0
    ? parsedImprovementId
    : null
  const improvementRequestId = useMemo(() => createRequestId('insight-evolve'), [improvementId])
  const [improvementOptions, setImprovementOptions] = useState<ImprovementView[]>([])
  const [improvementsLoading, setImprovementsLoading] = useState(false)
  const [improvementsError, setImprovementsError] = useState('')
  const [handoff, setHandoff] = useState<ImprovementHandoff | null>(null)
  const [handoffLoading, setHandoffLoading] = useState(false)
  const [handoffError, setHandoffError] = useState('')
  const [handoffReloadKey, setHandoffReloadKey] = useState(0)
  const [submitted, setSubmitted] = useState(false)
  const [createdTaskId, setCreatedTaskId] = useState('')
  const [taskName, setTaskName] = useState('')
  const [remark, setRemark] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [judgeBackend, setJudgeBackend] = useState<'subagent' | 'api'>('subagent')
  const [diagnoseSessionSource, setDiagnoseSessionSource] = useState<'local' | 'service_export'>('local')
  const [diagnoseModel, setDiagnoseModel] = useState('GLM-5.1')
  const [lookbackDays, setLookbackDays] = useState('3')
  const [maxDiagnoseSessions, setMaxDiagnoseSessions] = useState('10')
  const [badCaseCount, setBadCaseCount] = useState('4')
  const [goodCaseCount, setGoodCaseCount] = useState('1')
  const [focusIssue, setFocusIssue] = useState('影响任务完成率的主要问题，优先关注工具调用失败、任务未完成和未经验证的回答')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [runtimeMaintenance, setRuntimeMaintenance] = useState(true)
  const [bots, setBots] = useState<TCLogBot[]>([])
  const [botId, setBotId] = useState('')
  const [botEnv, setBotEnv] = useState('')
  const [botSelectionKey, setBotSelectionKey] = useState('')
  const [botsLoading, setBotsLoading] = useState(false)
  const [botsError, setBotsError] = useState('')
  const [diagnosisTasks, setDiagnosisTasks] = useState<EvolveTask[]>([])
  const [sourceDiagnosisTaskIds, setSourceDiagnosisTaskIds] = useState<string[]>([])
  const [maxRounds, setMaxRounds] = useState('3')
  const [benchObjective, setBenchObjective] = useState('')
  const [evolutionGoal, setEvolutionGoal] = useState('')
  const [fullInputMode, setFullInputMode] = useState<FullInputMode>('diagnose_goal')
  const [startDate, setStartDate] = useState(() => dateValue(-3))
  const [endDate, setEndDate] = useState(() => dateValue())
  const [customCommands, setCustomCommands] = useState(false)
  const [nodeCommandYamls, setNodeCommandYamls] = useState<Record<string, string>>({})
  const [nodeDefinitions, setNodeDefinitions] = useState<Record<string, NodeDefinition[]>>({})
  const [insightNodeDefinitions, setInsightNodeDefinitions] = useState<NodeDefinition[]>([])
  const currentUserId = user?.userId ?? ''
  const forceMessage = false
  const [crossBotConfirmed, setCrossBotConfirmed] = useState(false)
  const [benchDomains, setBenchDomains] = useState<BenchDomain[]>([])
  const [benchDomainId, setBenchDomainId] = useState('')
  const [trainBenchDomainId, setTrainBenchDomainId] = useState('')
  const [testBenchDomainId, setTestBenchDomainId] = useState('')
  const [benchDomainsError, setBenchDomainsError] = useState('')
  const requestedPackId = searchParams.get('packId')?.trim() ?? ''
  const requestedSourceTaskId = searchParams.get('sourceTaskId')?.trim() ?? ''
  const requestedSourceKind = searchParams.get('sourceKind')?.trim() ?? ''
  const requestedSourceRound = Number.parseInt(searchParams.get('sourceRound')?.trim() ?? '', 10)
  const requestedBotEnv = searchParams.get('botEnv')?.trim() ?? ''
  const [restorePacks, setRestorePacks] = useState<Awaited<ReturnType<typeof api.evolve.listPacks>>['items']>([])
  const [restorePacksLoading, setRestorePacksLoading] = useState(false)
  const [restorePacksError, setRestorePacksError] = useState('')
  const [restorePackId, setRestorePackId] = useState(requestedPackId)
  const autoRestoreTaskName = useRef('')
  const activeHandoff = improvementSource && handoff?.improvement.improvementId === improvementId ? handoff : null
  const autoRepairAuthorization = activeHandoff?.improvement.actionType === 'DIRECT_EVOLUTION'
  const trustedAutoRepairAuthorization = autoRepairAuthorization
    && activeHandoff?.improvement.adminReviewStatus === 'TRUSTED'
  const evolveUserId = currentUserId
  const visibleHandoffError = improvementSource && rawImprovementId !== '' && improvementId === null
    ? '改进项 ID 必须是正整数，无法读取 Insight Center 交接信息。'
    : handoffError
  const selectedRestorePack = restorePacks.find((pack) => pack.packId === restorePackId)

  useEffect(() => {
    let active = true
    api.evolve.taskDefinitions().then((result) => {
      if (!active) return
      setNodeDefinitions(Object.fromEntries(result.tasks.map((item) => [item.type, item.nodes])))
      setInsightNodeDefinitions(result.variants.insight_improvement)
    }).catch((error) => {
      if (active) setSubmitError(error instanceof Error ? error.message : '节点命令定义加载失败')
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!improvementSource || authState === 'loading' || !currentUserId) return
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setImprovementsLoading(true)
      setImprovementsError('')
      void insightApi.improvements({ pageSize: 50 })
        .then((result) => {
          if (active) setImprovementOptions(result.items)
        })
        .catch((error) => {
          if (!active) return
          setImprovementOptions([])
          setImprovementsError(error instanceof Error ? error.message : '治理项列表加载失败')
        })
        .finally(() => {
          if (active) setImprovementsLoading(false)
        })
    })
    return () => { active = false }
  }, [authState, currentUserId, improvementSource])

  useEffect(() => {
    if (!improvementSource || improvementId === null) return
    if (authState === 'loading' || !currentUserId) return
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setHandoffLoading(true)
      setHandoffError('')
      void insightApi.improvementHandoff(improvementId)
        .then((result) => {
          if (!active) return
          setHandoff(result)
          setCrossBotConfirmed(false)
          setTaskName(improvementTaskName(result))
          setRemark(improvementRemark(result))
        })
        .catch((error) => {
          if (!active) return
          setHandoff(null)
          setHandoffError(error instanceof Error ? error.message : '改进项交接信息加载失败')
        })
        .finally(() => {
          if (active) setHandoffLoading(false)
        })
    })
    return () => { active = false }
  }, [authState, currentUserId, handoffReloadKey, improvementId, improvementSource])

  useEffect(() => {
    let active = true
    if (!evolveUserId) {
      queueMicrotask(() => {
        if (!active) return
        setBots([])
        setBotId('')
        setBotEnv('')
        setBotSelectionKey('')
      })
      return () => { active = false }
    }
    const sourceBotId = activeHandoff?.improvement.botOwnerUserId === evolveUserId
      ? activeHandoff.improvement.botId
      : undefined
    queueMicrotask(() => {
      if (!active) return
      setBotsLoading(true)
      setBotsError('')
      void api.tclog.bots({ ownerId: evolveUserId, status: 'all' })
        .then((result) => {
          if (!active) return
          const availableBots = result.bots
          setBots(availableBots)
          const sourceBot = sourceBotId
            ? availableBots.find((bot) => bot.botId === sourceBotId)
            : undefined
          setBotSelectionKey(sourceBot ? evolveBotOptionKey(sourceBot) : '')
          setBotId(sourceBot?.botId ?? '')
          setBotEnv(sourceBot?.env ?? '')
        })
        .catch((error) => {
          if (!active) return
          setBots([])
          setBotId('')
          setBotEnv('')
          setBotSelectionKey('')
          setBotsError(error instanceof Error ? error.message : 'Bot 列表加载失败')
        })
        .finally(() => {
          if (active) setBotsLoading(false)
        })
    })
    return () => { active = false }
  }, [activeHandoff?.improvement.botId, activeHandoff?.improvement.botOwnerUserId, evolveUserId])

  useEffect(() => {
    if (taskType !== 'optimize' || !evolveUserId || !botId) return
    void api.evolve.listTasks().then(({ tasks }) => {
      const eligible = tasks.filter((task) =>
        task.user_id === evolveUserId && task.bot_id === botId
        && String(task.config?.botEnv ?? '') === botEnv
        && task.steps?.some((item) => item.stepType === 'diagnose' && item.status === 'succeeded')
        && task.steps?.some((item) => item.stepType === 'plan' && item.status === 'succeeded'))
      setDiagnosisTasks(eligible)
      setSourceDiagnosisTaskIds((current) => current.filter((id) => eligible.some((task) => task.task_id === id)))
    }).catch(() => setDiagnosisTasks([]))
  }, [taskType, evolveUserId, botId, botEnv])

  useEffect(() => {
    if (taskType !== 'pack_restore' || !evolveUserId) return
    let active = true
    setRestorePacksLoading(true)
    setRestorePacksError('')
    void api.evolve.listPacks()
      .then((result) => {
        if (!active) return
        const available = result.items.filter((pack) => pack.status === 'available' || !pack.status)
        setRestorePacks(available)
        setRestorePackId((current) => {
          if (requestedPackId) {
            return available.find((pack) => pack.packId === requestedPackId)?.packId ?? ''
          }
          if (requestedSourceTaskId && requestedSourceKind) {
            const requested = available.find((pack) => pack.taskId === requestedSourceTaskId
              && pack.sourceKind === requestedSourceKind
              && (requestedSourceKind !== 'round'
                || (Number.isFinite(requestedSourceRound) && pack.sourceRound === requestedSourceRound)))
            return requested?.packId ?? ''
          }
          return available.some((pack) => pack.packId === current) ? current : ''
        })
      })
      .catch((error) => {
        if (!active) return
        setRestorePacks([])
        setRestorePackId('')
        setRestorePacksError(error instanceof Error ? error.message : 'Pack 版本加载失败')
      })
      .finally(() => { if (active) setRestorePacksLoading(false) })
    return () => { active = false }
  }, [evolveUserId, requestedPackId, requestedSourceKind, requestedSourceRound, requestedSourceTaskId, taskType])

  useEffect(() => {
    if (taskType !== 'pack_restore' || !selectedRestorePack || bots.length === 0) return
    let active = true
    queueMicrotask(() => {
      if (!active) return
      const sourceBot = bots.find((bot) => bot.botId === selectedRestorePack.botId
        && (!requestedBotEnv || (bot.env ?? '') === requestedBotEnv))
        ?? bots.find((bot) => bot.botId === selectedRestorePack.botId)
      if (sourceBot) {
        setBotSelectionKey(evolveBotOptionKey(sourceBot))
        setBotId(sourceBot.botId)
        setBotEnv(sourceBot.env ?? '')
      }
      const nextTaskName = packRestoreTaskName(selectedRestorePack)
      setTaskName((current) => !current.trim() || current === autoRestoreTaskName.current ? nextTaskName : current)
      autoRestoreTaskName.current = nextTaskName
    })
    return () => { active = false }
  }, [bots, requestedBotEnv, selectedRestorePack, taskType])

  useEffect(() => {
    if ((taskType !== 'bench' && taskType !== 'bench_optimize') || !evolveUserId) return
    let active = true
    void api.bench.domains().then((domains) => {
      if (!active) return
      const available = domains.filter((domain) => domain.ownerUserId === evolveUserId && domain.status === 'active')
      setBenchDomains(available)
      setBenchDomainId((current) => available.some((domain) => domain.domainId === current) ? current : (available[0]?.domainId ?? ''))
      setTrainBenchDomainId((current) => available.some((domain) => domain.domainId === current) ? current : (available[0]?.domainId ?? ''))
      setTestBenchDomainId((current) => available.some((domain) => domain.domainId === current) ? current : (available[1]?.domainId ?? available[0]?.domainId ?? ''))
      setBenchDomainsError('')
    }).catch((error) => {
      if (!active) return
      setBenchDomains([])
      setBenchDomainId('')
      setBenchDomainsError(error instanceof Error ? error.message : 'Bench Domain 加载失败')
    })
    return () => { active = false }
  }, [taskType, evolveUserId])

  const diagnoseEnabled = taskType === 'diagnose'
    || (taskType === 'full' && !improvementSource && fullInputMode === 'diagnose_goal')
  const selectedBot = bots.find((bot) => bot.botId === botId && (bot.env ?? '') === botEnv)
  const arcaSelected = selectedBot?.deviceProvider?.toLowerCase() === 'arca'
  const serviceRuntimeSelected = selectedBot?.botType?.toLowerCase() === 'service'
  const serviceSourceAvailable = serviceRuntimeSelected || Boolean(selectedBot?.hasServiceBot)
  const effectiveJudgeBackend = arcaSelected ? 'subagent' : judgeBackend
  const crossBotTarget = Boolean(activeHandoff && botId && (
    activeHandoff.improvement.botOwnerUserId !== evolveUserId
    || activeHandoff.improvement.botId !== botId
  ))
  const diagnoseIntent = buildDiagnoseIntent({
    lookbackDays: Number(lookbackDays),
    startDate,
    endDate,
    useDateRange: taskType === 'full',
    badCaseCount: Number(badCaseCount),
    goodCaseCount: Number(goodCaseCount),
    focusIssue,
  })

  useEffect(() => {
    if (diagnoseEnabled && serviceRuntimeSelected && diagnoseSessionSource !== 'service_export') {
      setDiagnoseSessionSource('service_export')
    } else if (diagnoseSessionSource === 'service_export' && !serviceSourceAvailable) {
      setDiagnoseSessionSource('local')
    }
  }, [diagnoseEnabled, diagnoseSessionSource, serviceRuntimeSelected, serviceSourceAvailable])

  if (!taskType) {
    return <TaskList />
  }

  // apply 仅作为历史任务类型保留；新应用统一使用 pack_restore 表单。
  if (taskType === 'apply') {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-gray-950">“发起应用”已下线</h1>
          <p className="mt-2 text-sm text-gray-500">请选择已登记的 Pack，并通过恢复任务应用到 Bot。</p>
          <button className={`${primaryButton} mt-6`} onClick={() => navigate('/evolve/new?type=pack_restore')}>发起应用 Pack</button>
        </div>
      </div>
    )
  }

  const taskCopy = improvementSource ? (autoRepairAuthorization ? {
    eyebrow: '自动修复授权',
    title: '确认并授权 Agent 自动修复',
    description: trustedAutoRepairAuthorization
      ? '首次确认后立即执行；以后同一 Bot 命中同一治理规则版本和风险范围时，将自动进入修复与验收。'
      : '本次确认后立即执行，并保存 Owner 授权；以后授权范围完全一致的同类项经 Admin 批准后，将自动进入修复与验收，无需再次向你确认。',
    submit: '授权本次及后续同类自动修复',
  } : {
    eyebrow: '治理优化',
    title: '发起治理优化',
    description: '选择治理项和目标 Bot，生成 Spec 并执行优化 Loop。',
    submit: '创建治理优化任务',
  }) : ({
    diagnose: {
      eyebrow: 'Bot诊断',
      title: '发起 Bot 诊断',
      description: '诊断近期使用情况，产出 Goal、Spec v0 和 Bench Case。',
      submit: '创建诊断任务',
    },
    optimize: {
      eyebrow: '诊断后优化',
      title: '基于诊断继续优化',
      description: '复用已有 Diagnose 和 Plan 结果，直接运行优化 Loop。',
      submit: '创建诊断后优化任务',
    },
    full: {
      eyebrow: 'Bot自进化',
      title: '发起 Bot 自进化全流程',
      description: fullInputMode === 'direct_goal'
        ? '根据一句话目标生成 Bench，并执行规划和多轮优化。'
        : '分析历史 Session，并结合优化目标执行规划和多轮优化。',
      submit: '创建 Bot自进化任务',
    },
    bench: {
      eyebrow: 'Bench诊断',
      title: '使用已发布模板评测 Bot',
      description: '直接执行一次完整 Bench，并在进化任务中查看指标和报告。',
      submit: '创建 Bench 任务',
    },
    bench_optimize: {
      eyebrow: 'Bench优化',
      title: '使用 Bench 驱动 Bot 优化',
      description: '训练 Domain 用于优化，测试 Domain 用于独立验证。每个 Domain 可包含多个模板。',
      submit: '创建 Bench优化任务',
    },
    pack: {
      eyebrow: '创建Pack', title: '保存当前 Bot 环境', description: '创建独立环境快照并上传 OSS，供后续应用。', submit: '创建 Pack 任务',
    },
    pack_restore: {
      eyebrow: '应用Pack', title: '应用 Bot 环境', description: '将已选择的历史 Pack 应用到 Bot。', submit: '创建应用任务',
    },
    runtime_cleanup: {
      eyebrow: '任务清理', title: '清理进化运行记录', description: '清理目标 Bot 草稿环境中的历史 ClawEvolve Agent 与 Session。', submit: '创建清理任务',
    },
  }[taskType])

  if (submitted) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 text-emerald-600"><Icon name="check" className="h-6 w-6" /></span>
          <h1 className="mt-5 text-xl font-semibold text-gray-950">{taskCopy.eyebrow}任务已创建</h1>
          <p className="mt-2 text-sm text-gray-500">ClawWeb 将向目标 Bot 发送对应阶段命令。</p>
          <div className="mx-auto mt-5 max-w-md rounded-xl bg-gray-50 p-4 text-left">
            <SummaryRow label="进化对象" value={`${selectedBot?.ownerId || evolveUserId} / ${botId}`} mono />
            <SummaryRow label="任务名称" value={taskName} />
            <SummaryRow label="任务 ID" value={createdTaskId} mono />
            <SummaryRow label="任务类型" value={taskCopy.eyebrow} />
            {taskType === 'full' && !improvementSource && <SummaryRow label="进化方式" value={fullInputMode === 'direct_goal' ? '按目标进化' : '先诊断再进化'} />}
            {taskType === 'full' && evolutionGoal && <SummaryRow label="优化目标" value={evolutionGoal} />}
          </div>
          <button className={`${primaryButton} mt-6`} onClick={() => navigate(`/evolve/runs/${createdTaskId}?type=${taskType}`)}>查看进化任务 <Icon name="arrow" /></button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve')} className="mb-5 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800"><span className="rotate-180"><Icon name="arrow" /></span>返回任务列表</button>
      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 py-5">
          <div className="flex items-center gap-2 text-sm font-medium text-blue-600"><Icon name="spark" />{taskCopy.eyebrow}</div>
          <h1 className="mt-2 text-xl font-semibold text-gray-950">{taskCopy.title}</h1>
          <p className="mt-1 text-sm text-gray-500">{taskCopy.description}</p>
          {improvementSource && <div className={`mt-4 rounded-xl border px-4 py-3 ${visibleHandoffError || improvementsError ? 'border-red-200 bg-red-50' : 'border-indigo-200 bg-indigo-50/70'}`}>
            <label className="block">
              <span className={`mb-1.5 block text-xs font-semibold ${visibleHandoffError || improvementsError ? 'text-red-800' : 'text-indigo-800'}`}>治理项 <span className="text-red-500">*</span></span>
              <select
                aria-label="治理项"
                className={inputClass}
                value={improvementId ?? ''}
                disabled={improvementsLoading}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams)
                  if (event.target.value) next.set('improvementId', event.target.value)
                  else next.delete('improvementId')
                  setHandoff(null)
                  setHandoffError('')
                  setTaskName('')
                  setRemark('')
                  setCrossBotConfirmed(false)
                  setSearchParams(next, { replace: true })
                }}
              >
                <option value="">{improvementsLoading ? '正在加载治理项…' : '请选择治理项'}</option>
                {improvementOptions.map((item) => <option key={item.improvementId} value={item.improvementId}>#{item.improvementId} · {item.title}（{improvementStatusLabel(item.status)} · {item.evidenceCount} 条 Evidence）</option>)}
              </select>
            </label>
            {improvementsError && <p className="mt-2 text-xs text-red-700">{improvementsError}</p>}
            {!improvementsLoading && !improvementsError && improvementOptions.length === 0 && <p className="mt-2 text-xs text-amber-700">当前没有可用的治理项。</p>}
            <div className="mt-3 flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <span className={`mt-0.5 ${visibleHandoffError ? 'text-red-600' : 'text-blue-600'}`}><Icon name="file" /></span>
                <div className="min-w-0">
                  <p className={`text-xs font-semibold ${visibleHandoffError ? 'text-red-800' : 'text-blue-800'}`}>治理输入</p>
                  {handoffLoading ? <p className="mt-1 text-xs text-blue-600">正在读取冻结证据摘要与用户判断…</p>
                    : visibleHandoffError ? <p className="mt-1 break-words text-xs text-red-700">{visibleHandoffError}</p>
                    : activeHandoff ? <><p className="mt-1 truncate text-sm font-medium text-blue-950">{activeHandoff.improvement.title}</p><p className="mt-1 font-mono text-[10px] text-blue-600">{activeHandoff.improvement.improvementId} · {activeHandoff.improvement.evidenceCount} 个失败 Task</p><p className="mt-1 text-[11px] text-blue-700">Evidence 来源：{activeHandoff.improvement.botOwnerUserId} / {activeHandoff.improvement.botId}</p></>
                    : <p className="mt-1 text-xs text-blue-600">选择治理项后，将读取冻结 Evidence 和用户判断。</p>}
                </div>
              </div>
              {visibleHandoffError && improvementId && <button onClick={() => setHandoffReloadKey((value) => value + 1)} className="shrink-0 text-xs font-medium text-red-700 hover:text-red-900">重试</button>}
            </div>
          </div>}
        </div>

        <div className="p-6">
          <div className="grid gap-7 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          <div className="space-y-6">
          <section>
            <h2 className="text-sm font-semibold text-gray-900">任务信息</h2>
            <div className="mt-3 space-y-4">
              <label>
                <span className="mb-1.5 block text-xs font-medium text-gray-600">任务名称 <span className="text-red-500">*</span></span>
                <input
                  className={inputClass}
                  value={taskName}
                  maxLength={128}
                  onChange={(event) => setTaskName(event.target.value)}
                  placeholder={`例如：客服 Bot ${taskCopy.eyebrow}任务`}
                />
              </label>
              <label>
                <span className="mb-1.5 block text-xs font-medium text-gray-600">备注 <span className="font-normal text-gray-400">（可选）</span></span>
                <textarea
                  className={`${inputClass} min-h-20 resize-y`}
                  value={remark}
                  maxLength={1000}
                  onChange={(event) => setRemark(event.target.value)}
                  placeholder="补充本次任务的背景、范围或注意事项"
                />
                <span className="mt-1 block text-right text-[11px] text-gray-400">{remark.length}/1000</span>
              </label>
            </div>
          </section>

          <section>
            <h2 className="text-sm font-semibold text-gray-900">进化对象</h2>
            <div className="mt-3">
              <label><span className="mb-1.5 block text-xs font-medium text-gray-600">用户空间 user_id</span><input className={inputClass} value={authState === 'loading' ? '正在识别当前用户…' : evolveUserId} readOnly /></label>
              <div className="mt-4">
                <span className="mb-1.5 block text-xs font-medium text-gray-600">目标 Bot</span>
                <EvolveBotPicker
                  bots={bots}
                  value={botSelectionKey}
                  disabled={botsLoading || !evolveUserId}
                  emptyText={botsLoading ? '正在加载 Bot…' : '当前用户没有可用 Bot'}
                  onChange={(key, bot) => {
                    setBotSelectionKey(key)
                    setBotId(bot.botId)
                    setBotEnv(bot.env ?? '')
                    setCrossBotConfirmed(false)
                    if (bot.deviceProvider?.toLowerCase() === 'arca') {
                      setJudgeBackend('subagent')
                      setApiKey('')
                    }
                  }}
                />
              </div>
            </div>
            {botsError && <p className="mt-2 text-xs text-red-600">{botsError}</p>}
            {!botsLoading && evolveUserId && bots.length === 0 && !botsError && <p className="mt-2 text-xs text-amber-600">当前用户没有可用 Bot，请先初始化 Bot 权限数据。</p>}
            {selectedBot && <div className="mt-3 flex items-center justify-between rounded-xl border border-blue-100 bg-blue-50/60 p-3">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white"><Icon name="bot" /></span>
                <div><p className="text-sm font-medium text-gray-900">{selectedBot.botName || selectedBot.displayBotId}</p><p className="mt-0.5 font-mono text-[11px] text-gray-500">{selectedBot.ownerId || evolveUserId} / {selectedBot.botId} / {selectedBot.env || '环境未知'}</p><p className="mt-1 text-[11px] text-gray-600">执行方式：{botDispatchPlan(selectedBot.deviceProvider)}</p></div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${selectedBot.deviceProvider?.toLowerCase() === 'baas' ? 'bg-blue-100 text-blue-700' : selectedBot.deviceProvider?.toLowerCase() === 'arca' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'}`}>{botProviderLabel(selectedBot.deviceProvider)}{selectedBot.deviceProvider?.toLowerCase() === 'baas' ? ' · 推荐' : selectedBot.deviceProvider?.toLowerCase() === 'arca' ? ' · 不推荐' : ''}</span>
                {selectedBot.accessType === 'collaborator' && <span className="rounded-full bg-violet-100 px-2.5 py-1 text-xs font-medium text-violet-700">协作 Bot</span>}
                <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${isOpenClawBot(selectedBot) ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>{selectedBot.activeEngine || '引擎未知'}</span>
                <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">{selectedBot.hasServiceBot ? '已有服务 Bot' : selectedBot.botType === 'service' ? '服务型 Bot' : '普通 Bot'}</span>
                <span className="text-xs font-medium text-emerald-600">已选择</span>
              </div>
            </div>}
            {selectedBot && !isOpenClawBot(selectedBot) && <p className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">当前进化流程仅支持 OpenClaw 引擎。该 Bot 的引擎为 {selectedBot.activeEngine}，可查看但暂不能发起进化。</p>}
            {arcaSelected && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">ARCA Bot 会先通过 Message 初始化 Skill，再执行具体节点；诊断仅支持 Agent Judge，不接收 API Key。</p>}
            {activeHandoff && botId && crossBotTarget && <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-xl border border-amber-200 bg-amber-50/70 p-3">
              <input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-amber-300 text-amber-600" checked={crossBotConfirmed} onChange={(event) => setCrossBotConfirmed(event.target.checked)} />
              <span><span className="block text-sm font-medium text-amber-900">确认使用跨 Bot Evidence</span><span className="mt-0.5 block text-xs leading-5 text-amber-700">失败证据来自 {activeHandoff.improvement.botOwnerUserId} / {activeHandoff.improvement.botId}，实际修改将在 {evolveUserId} / {botId} 中执行。Plan 会先判断问题是否适用于当前 Workspace。</span></span>
            </label>}
            {autoRepairAuthorization && activeHandoff && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50/70 p-4">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 text-emerald-700"><Icon name="check" /></span>
                <div>
                  <p className="text-sm font-semibold text-emerald-950">这是一次持久授权，不是每次弹窗确认</p>
                  <p className="mt-1 text-xs leading-5 text-emerald-800">授权范围仅限当前用户、目标 Bot、治理规则 <span className="font-mono">{activeHandoff.improvement.sourceRuleId || '—'}</span> 的当前版本、允许修改目标和风险等级。规则升级、修改范围或风险变化后会自动失效并要求重新授权；你也可以随时在 Insight Center 撤销。</p>
                  <p className={`mt-2 text-xs font-medium ${trustedAutoRepairAuthorization ? 'text-emerald-800' : 'text-amber-800'}`}>{trustedAutoRepairAuthorization ? 'Admin 已信任这条规则：后续精确命中授权范围时会自动推进。' : 'Admin 当前仅批准本次：Owner 授权会保留；规则被 Admin 标记可信前，后续仍需 Admin 先确认。'}</p>
                </div>
              </div>
            </div>}
          </section>

          {taskType === 'full' && !improvementSource && <FullFlowFields
            mode={fullInputMode}
            onModeChange={setFullInputMode}
            goal={evolutionGoal}
            onGoalChange={setEvolutionGoal}
          />}

          {(taskType === 'diagnose' || (taskType === 'full' && !improvementSource && fullInputMode === 'diagnose_goal')) && <DiagnoseFields
            sessionSource={diagnoseSessionSource}
            serviceSourceAvailable={serviceSourceAvailable}
            onSessionSourceChange={setDiagnoseSessionSource}
            judgeBackend={effectiveJudgeBackend}
            apiJudgeDisabled={arcaSelected}
            onJudgeBackendChange={(value) => {
              setJudgeBackend(value)
              if (value === 'subagent') setApiKey('')
              if (value === 'api' && !diagnoseModel.trim()) setDiagnoseModel('GLM-5.1')
            }}
            apiKey={apiKey}
            onApiKeyChange={setApiKey}
            model={diagnoseModel}
            onModelChange={setDiagnoseModel}
            startDate={startDate}
            endDate={endDate}
            onStartDateChange={setStartDate}
            onEndDateChange={setEndDate}
            dateRangeEnabled={taskType === 'full'}
            lookbackDays={lookbackDays}
            onLookbackDaysChange={setLookbackDays}
            maxDiagnoseSessions={maxDiagnoseSessions}
            onMaxDiagnoseSessionsChange={setMaxDiagnoseSessions}
            badCaseCount={badCaseCount}
            goodCaseCount={goodCaseCount}
            onBadCaseCountChange={setBadCaseCount}
            onGoodCaseCountChange={setGoodCaseCount}
            focusIssue={focusIssue}
            onFocusIssueChange={setFocusIssue}
            diagnoseIntent={diagnoseIntent}
          />}
          {taskType === 'optimize' && <OptimizeFields botSelected={Boolean(botId)} tasks={diagnosisTasks} selectedTaskIds={sourceDiagnosisTaskIds} onTaskIdsChange={setSourceDiagnosisTaskIds} />}
          {taskType === 'bench' && <BenchFields domains={benchDomains} domainId={benchDomainId} onDomainIdChange={setBenchDomainId} error={benchDomainsError} />}
          {taskType === 'bench_optimize' && <BenchOptimizeFields domains={benchDomains} trainDomainId={trainBenchDomainId} testDomainId={testBenchDomainId} onTrainDomainIdChange={setTrainBenchDomainId} onTestDomainIdChange={setTestBenchDomainId} error={benchDomainsError} />}
          {taskType === 'pack_restore' && <PackRestoreFields packs={restorePacks} selectedPackId={restorePackId} onPackIdChange={setRestorePackId} loading={restorePacksLoading} error={restorePacksError} />}
          {taskType === 'runtime_cleanup' && <section className="border-t border-gray-100 pt-6"><h2 className="text-sm font-semibold text-gray-900">清理范围</h2><div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">执行清理前会重启所选 Bot 草稿环境的 Gateway，当前草稿会话可能中断。仅清理带明确 ClawEvolve 任务标记的历史 Agent 与 Session；不会清理普通业务 Session、业务 Skill、Pack、Bench 日志或 clawevolve_results。若仍有进化任务运行，系统会要求再次确认后才能强制清理。</div></section>}
          {taskType === 'bench_optimize' && <section className="border-t border-gray-100 pt-6"><h2 className="text-sm font-semibold text-gray-900">优化目标</h2><label className="mt-3 block"><span className="mb-1.5 block text-xs font-medium text-gray-600">目标、成功标准和约束 <span className="text-red-500">*</span></span><textarea className={`${inputClass} min-h-28 resize-y`} value={benchObjective} onChange={(event) => setBenchObjective(event.target.value)} placeholder="例如：提升博客的结构完整性、事实准确性和语言表达，测试集得分不低于 0.9，不得针对测试用例硬编码。" /></label></section>}
          {(taskType === 'diagnose' || taskType === 'full' || taskType === 'optimize' || taskType === 'bench' || taskType === 'bench_optimize') && <NodeCommandYamlFields definitions={improvementSource && taskType === 'full'
            ? insightNodeDefinitions
            : taskType === 'full' && fullInputMode === 'direct_goal'
              ? (nodeDefinitions.full ?? []).filter((node) => node.key !== 'diagnose')
              : (nodeDefinitions[taskType] ?? [])} expanded={customCommands} onExpandedChange={setCustomCommands} values={nodeCommandYamls} onChange={setNodeCommandYamls} />}

          {taskType === 'optimize' || taskType === 'full' || taskType === 'bench_optimize' ? <section className="border-t border-gray-100 pt-6">
            <h2 className="text-sm font-semibold text-gray-900">优化迭代</h2>
            <label className="mt-3 block max-w-xs"><span className="mb-1.5 block text-xs font-medium text-gray-600">最大优化轮数 <span className="font-normal text-gray-400">（上限 100 轮）</span></span><input className={inputClass} type="number" min={1} max={100} step={1} inputMode="numeric" value={maxRounds} onChange={(event) => setMaxRounds(event.target.value)} placeholder="请输入 1 到 100" /></label>
            <p className="mt-2 text-xs text-gray-400">{taskType === 'full' && fullInputMode === 'direct_goal' ? 'Plan 只执行一次；' : '诊断只执行一次；'}只有优化阶段会按验证结果进行多轮迭代，最多执行 100 轮。</p>
          </section> : null}
          {taskType !== 'pack' && taskType !== 'pack_restore' && taskType !== 'runtime_cleanup' && <RuntimeMaintenanceOption enabled={runtimeMaintenance} onChange={setRuntimeMaintenance} />}
          </div>
          <TaskFormOverview taskType={taskType} fullInputMode={fullInputMode} improvementSource={improvementSource} />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-100 bg-gray-50/60 px-6 py-4">
          {submitError && <p className="mr-auto text-xs text-red-600">{submitError}</p>}
          <button className={secondaryButton} onClick={() => navigate('/evolve')}>取消</button>
          <button disabled={submitting || botsLoading || restorePacksLoading || !currentUserId || !evolveUserId || !botId || !isOpenClawBot(selectedBot) || (taskType === 'pack_restore' && !selectedRestorePack) || (improvementSource && !activeHandoff) || (crossBotTarget && !crossBotConfirmed)} className={`${primaryButton} disabled:opacity-50`} onClick={async () => {
            if (!taskName.trim()) { setSubmitError('请输入任务名称'); return }
            if (!currentUserId) { setSubmitError('无法识别当前用户，请重新登录'); return }
            if (!botId) { setSubmitError('请选择目标 Bot'); return }
            if (!isOpenClawBot(selectedBot)) { setSubmitError(`当前仅支持 OpenClaw 引擎，所选 Bot 为 ${selectedBot?.activeEngine}`); return }
            if (diagnoseEnabled && arcaSelected && effectiveJudgeBackend === 'api') { setSubmitError('ARCA 模式只支持 Agent Judge，不支持传入 API Key'); return }
            if (diagnoseEnabled && diagnoseSessionSource === 'service_export' && !serviceSourceAvailable) { setSubmitError('所选 Bot 没有可导出的服务态 Session'); return }
            if (taskType === 'bench' && !benchDomainId) { setSubmitError('请选择 Bench Domain'); return }
            if (taskType === 'bench_optimize' && (!trainBenchDomainId || !testBenchDomainId)) { setSubmitError('请选择训练和测试 Bench Domain'); return }
            if (taskType === 'bench_optimize' && !benchObjective.trim()) { setSubmitError('请输入优化目标'); return }
            if (taskType === 'pack_restore' && !selectedRestorePack) { setSubmitError('请选择要应用的 Pack 版本'); return }
            if (taskType === 'full' && !improvementSource && !evolutionGoal.trim()) { setSubmitError('请输入一句话优化目标'); return }
            if (taskType === 'optimize' && sourceDiagnosisTaskIds.length === 0) { setSubmitError('请选择一个已完成 Plan 的诊断任务'); return }
            if (improvementSource && !activeHandoff) { setSubmitError('请先成功加载 Insight Center 改进项'); return }
            if (crossBotTarget && !crossBotConfirmed) { setSubmitError('请确认 Evidence 来源与实际执行目标不同'); return }
            if (diagnoseEnabled && effectiveJudgeBackend === 'api' && !apiKey.trim()) { setSubmitError('API Judge 模式请输入 API Key'); return }
            const parsedLookbackDays = Number(lookbackDays)
            const parsedMaxDiagnoseSessions = Number(maxDiagnoseSessions)
            const parsedBadCaseCount = Number(badCaseCount)
            const parsedGoodCaseCount = Number(goodCaseCount)
            if (diagnoseEnabled
              && (!Number.isSafeInteger(parsedLookbackDays) || parsedLookbackDays < 1 || parsedLookbackDays > 30
                || !Number.isSafeInteger(parsedMaxDiagnoseSessions) || parsedMaxDiagnoseSessions < 1 || parsedMaxDiagnoseSessions > 1000
                || !Number.isSafeInteger(parsedBadCaseCount) || parsedBadCaseCount < 0 || parsedBadCaseCount > 100
                || !Number.isSafeInteger(parsedGoodCaseCount) || parsedGoodCaseCount < 0 || parsedGoodCaseCount > 100
                || parsedBadCaseCount + parsedGoodCaseCount < 1 || parsedBadCaseCount + parsedGoodCaseCount > 100)) {
              setSubmitError('诊断范围需为最近 1 到 30 天，最多诊断 Session 数量需为 1 到 1000，Good/Bad Case 合计需为 1 到 100 个'); return
            }
            if (diagnoseEnabled && !focusIssue.trim()) {
              setSubmitError('请输入关注问题'); return
            }
            if (taskType === 'full' && !improvementSource && fullInputMode === 'diagnose_goal' && (!startDate || !endDate || startDate > endDate)) {
              setSubmitError('请选择合法的开始和结束日期'); return
            }
            const parsedMaxRounds = Number(maxRounds)
            if ((taskType === 'optimize' || taskType === 'full' || taskType === 'bench_optimize') && (!Number.isSafeInteger(parsedMaxRounds) || parsedMaxRounds < 1 || parsedMaxRounds > 100)) {
              setSubmitError('最大优化轮数必须是 1 到 100 的整数'); return
            }
            setSubmitting(true); setSubmitError('')
            try {
              const taskInfo = { taskName: taskName.trim(), remark: remark.trim() || undefined }
              const fullNodeCommandYamls = customCommands
                ? Object.fromEntries(Object.entries(nodeCommandYamls).filter(([node]) => fullInputMode === 'diagnose_goal' || node !== 'diagnose'))
                : undefined
              const input = {
                ...taskInfo, userId: evolveUserId, botId, botEnv, judgeBackend: effectiveJudgeBackend,
                sessionSource: diagnoseSessionSource,
                apiKey: effectiveJudgeBackend === 'api' ? apiKey.trim() : undefined, model: diagnoseModel,
                diagnoseIntent, maxSessions: parsedMaxDiagnoseSessions,
                goal: taskType === 'full' ? evolutionGoal.trim() : undefined,
                startDate, endDate, nodeCommandYamls: customCommands ? (improvementSource
                  ? Object.fromEntries(Object.entries(nodeCommandYamls).filter(([node]) => node === 'plan' || node === 'optimize'))
                  : nodeCommandYamls) : undefined, forceMessage, runtimeMaintenance: taskType === 'pack' || taskType === 'pack_restore' ? false : runtimeMaintenance,
              }
              const result = activeHandoff
                ? await api.evolve.createTask({
                    ...taskInfo,
                    taskType: 'full',
                    userId: evolveUserId,
                    botId,
                    botEnv,
                    input: {
                      type: 'insight_improvement',
                      improvementId: activeHandoff.improvement.improvementId,
                      crossBotConfirmed,
                      persistAutoRepairGrant: autoRepairAuthorization,
                      adminAutoExecute: adminAutoExecute || undefined,
                      adminConsentToken: adminConsentToken || undefined,
                    },
                    maxRounds: parsedMaxRounds,
                    nodeCommandYamls: customCommands
                      ? Object.fromEntries(Object.entries(nodeCommandYamls).filter(([node]) => node === 'plan' || node === 'optimize'))
                      : undefined,
                    forceMessage,
                    runtimeMaintenance,
                  }, improvementRequestId)
                : taskType === 'optimize'
                ? await api.evolve.createOptimization({ ...taskInfo, userId: evolveUserId, botId, botEnv, sourceDiagnosisTaskIds, maxRounds: parsedMaxRounds, nodeCommandYamls: customCommands ? nodeCommandYamls : undefined, forceMessage, runtimeMaintenance })
                : taskType === 'bench'
                ? await api.evolve.createBench({ ...taskInfo, userId: evolveUserId, botId, botEnv, benchDomainId, model: 'antchat/GLM-5.1', suite: 'all', scene: 'claw-evolve-bench', nodeCommandYamls: customCommands ? nodeCommandYamls : undefined, forceMessage, runtimeMaintenance })
                : taskType === 'bench_optimize'
                ? await api.evolve.createBenchOptimization({ ...taskInfo, userId: evolveUserId, botId, botEnv, objective: benchObjective.trim(), trainBenchDomainId, testBenchDomainId, maxRounds: parsedMaxRounds, nodeCommandYamls: customCommands ? nodeCommandYamls : undefined, forceMessage, runtimeMaintenance })
                : taskType === 'full'
                ? fullInputMode === 'direct_goal'
                  ? await api.evolve.createTask({
                      ...taskInfo, taskType: 'full', inputMode: 'direct_goal', userId: evolveUserId, botId, botEnv,
                      goal: evolutionGoal.trim(), maxRounds: parsedMaxRounds, nodeCommandYamls: fullNodeCommandYamls,
                      forceMessage, runtimeMaintenance,
                    })
                  : await api.evolve.createTask({
                      ...input, taskType: 'full', inputMode: 'diagnose_goal', maxRounds: parsedMaxRounds,
                      nodeCommandYamls: fullNodeCommandYamls,
                    })
                : taskType === 'pack'
                ? await api.evolve.createPack({ ...taskInfo, userId: evolveUserId, botId, botEnv, forceMessage, runtimeMaintenance: false })
                : taskType === 'pack_restore' && selectedRestorePack
                ? await api.evolve.restorePack({ ...taskInfo, userId: evolveUserId, botId, botEnv, packId: selectedRestorePack.packId, sourceTaskId: selectedRestorePack.taskId, sourceKind: selectedRestorePack.sourceKind, sourceRound: selectedRestorePack.sourceRound ?? undefined, forceMessage, runtimeMaintenance: false })
                : taskType === 'runtime_cleanup'
                ? await api.evolve.createRuntimeCleanup({ ...taskInfo, userId: evolveUserId, botId, botEnv, forceCleanup: false }).catch(async (error) => {
                    if (!(error instanceof Error) || !error.message.includes('EVOLVE_TASKS_STILL_RUNNING')) throw error
                    const confirmed = window.confirm('该 Bot 仍有进化任务运行中。强制清理可能删除这些任务正在使用的进化 Agent 和 Session，导致任务失败。是否继续？')
                    if (!confirmed) throw new Error('已取消强制清理')
                    return api.evolve.createRuntimeCleanup({ ...taskInfo, userId: evolveUserId, botId, botEnv, forceCleanup: true })
                  })
                : await api.evolve.createDiagnosis(input)
              setApiKey(''); setCreatedTaskId(result.task_id); setSubmitted(true)
            } catch (error) {
              setSubmitError(error instanceof Error ? error.message : '创建失败')
            } finally { setSubmitting(false) }
          }}><Icon name="send" />{submitting ? '正在发送…' : taskCopy.submit}</button>
        </div>
      </div>
    </div>
  )
}

function DiagnoseFields({
  sessionSource, serviceSourceAvailable, onSessionSourceChange,
  judgeBackend, apiJudgeDisabled, onJudgeBackendChange, apiKey, onApiKeyChange, model, onModelChange, startDate, endDate,
  onStartDateChange, onEndDateChange, dateRangeEnabled, lookbackDays,
  onLookbackDaysChange, maxDiagnoseSessions, onMaxDiagnoseSessionsChange,
  badCaseCount, goodCaseCount, onBadCaseCountChange,
  onGoodCaseCountChange, focusIssue, onFocusIssueChange, diagnoseIntent,
}: {
  sessionSource: 'local' | 'service_export'
  serviceSourceAvailable: boolean
  onSessionSourceChange: (value: 'local' | 'service_export') => void
  judgeBackend: 'subagent' | 'api'
  apiJudgeDisabled: boolean
  onJudgeBackendChange: (value: 'subagent' | 'api') => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  model: string
  onModelChange: (value: string) => void
  startDate: string
  endDate: string
  onStartDateChange: (value: string) => void
  onEndDateChange: (value: string) => void
  dateRangeEnabled: boolean
  lookbackDays: string
  onLookbackDaysChange: (value: string) => void
  maxDiagnoseSessions: string
  onMaxDiagnoseSessionsChange: (value: string) => void
  badCaseCount: string
  goodCaseCount: string
  onBadCaseCountChange: (value: string) => void
  onGoodCaseCountChange: (value: string) => void
  focusIssue: string
  onFocusIssueChange: (value: string) => void
  diagnoseIntent: string
}) {
  return (
    <>
      <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">诊断对象</h2>
        <p className="mt-1 text-xs leading-5 text-gray-500">选择本次用于提取诊断 Case 的 Bot 运行形态。</p>
        <div role="radiogroup" aria-label="诊断对象" className="mt-3 grid gap-3 sm:grid-cols-2">
          <button type="button" role="radio" aria-checked={sessionSource === 'local'} onClick={() => onSessionSourceChange('local')} className={`rounded-xl border p-4 text-left transition ${sessionSource === 'local' ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500/10' : 'border-gray-200 bg-white hover:border-blue-200'}`}>
            <div className="flex items-center justify-between gap-3">
              <span className={`text-sm font-semibold ${sessionSource === 'local' ? 'text-blue-800' : 'text-gray-800'}`}>个人 Bot</span>
              {sessionSource === 'local' && <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">已选择</span>}
            </div>
            <p className="mt-1.5 text-xs leading-5 text-gray-500">扫描个人 Bot 的历史 Session，执行筛选、Judge 和 Case 抽取。</p>
          </button>
          <button type="button" role="radio" aria-checked={sessionSource === 'service_export'} aria-disabled={!serviceSourceAvailable} disabled={!serviceSourceAvailable} onClick={() => onSessionSourceChange('service_export')} className={`rounded-xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${sessionSource === 'service_export' ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500/10' : 'border-gray-200 bg-white hover:border-blue-200'}`}>
            <div className="flex items-center justify-between gap-3">
              <span className={`text-sm font-semibold ${sessionSource === 'service_export' ? 'text-blue-800' : 'text-gray-800'}`}>服务 Bot</span>
              {sessionSource === 'service_export'
                ? <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">已选择</span>
                : !serviceSourceAvailable && <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">无服务态</span>}
            </div>
            <p className="mt-1.5 text-xs leading-5 text-gray-500">由当前草稿 Bot 安全导出同一业务 Bot 的服务态 Session 后诊断，不向服务运行实例发送消息或命令。</p>
          </button>
        </div>
      </section>
      <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">诊断范围</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {dateRangeEnabled ? <>
            <label><span className="mb-1.5 block text-xs font-medium text-gray-600">开始日期</span><input type="date" className={inputClass} value={startDate} max={endDate} onChange={(event) => onStartDateChange(event.target.value)} /></label>
            <label><span className="mb-1.5 block text-xs font-medium text-gray-600">结束日期</span><input type="date" className={inputClass} value={endDate} min={startDate} max={dateValue()} onChange={(event) => onEndDateChange(event.target.value)} /></label>
          </> : <label><span className="mb-1.5 block text-xs font-medium text-gray-600">会话时间范围</span><select className={inputClass} value={lookbackDays} onChange={(event) => onLookbackDaysChange(event.target.value)}><option value="3">最近 3 天</option><option value="7">最近 7 天</option><option value="14">最近 14 天</option><option value="30">最近 30 天</option></select></label>}
          <EvolveModelFields
            choice={EVOLVE_MODEL_OPTIONS.includes(model as (typeof EVOLVE_MODEL_OPTIONS)[number]) ? model : EVOLVE_CUSTOM_MODEL}
            customValue={EVOLVE_MODEL_OPTIONS.includes(model as (typeof EVOLVE_MODEL_OPTIONS)[number]) ? '' : model}
            onChoiceChange={(value) => onModelChange(value === EVOLVE_CUSTOM_MODEL ? '' : value)}
            onCustomValueChange={onModelChange}
            selectAriaLabel="诊断模型"
            customAriaLabel="诊断自定义模型名称"
            inputClassName={inputClass}
          />
          <label><span className="mb-1.5 block text-xs font-medium text-gray-600">最多诊断 Session 数量</span><input className={inputClass} type="number" min={1} max={1000} step={1} inputMode="numeric" value={maxDiagnoseSessions} onChange={(event) => onMaxDiagnoseSessionsChange(event.target.value)} /><span className="mt-1 block text-xs text-gray-400">最多送入 Judge 分析的候选 Session，默认 10。</span></label>
          <label><span className="mb-1.5 block text-xs font-medium text-gray-600">Bad Case 数量</span><input className={inputClass} type="number" min={0} max={100} step={1} inputMode="numeric" value={badCaseCount} onChange={(event) => onBadCaseCountChange(event.target.value)} /></label>
          <label><span className="mb-1.5 block text-xs font-medium text-gray-600">Good Case 数量</span><input className={inputClass} type="number" min={0} max={100} step={1} inputMode="numeric" value={goodCaseCount} onChange={(event) => onGoodCaseCountChange(event.target.value)} /></label>
          <div><span className="mb-1.5 block text-xs font-medium text-gray-600">Judge 方式</span><div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1"><button type="button" onClick={() => onJudgeBackendChange('subagent')} className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${judgeBackend === 'subagent' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-800'}`}>Agent Judge</button><button type="button" disabled={apiJudgeDisabled} onClick={() => onJudgeBackendChange('api')} className={`rounded-md px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${judgeBackend === 'api' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-800'}`}>自定义 API</button></div><span className="mt-1 block text-xs text-gray-400">{apiJudgeDisabled ? 'ARCA 模式仅支持 Agent Judge，不会通过 Message 传递 API Key。' : judgeBackend === 'subagent' ? '使用 Bot 当前 OpenClaw 模型，无需 API Key。' : '使用指定模型与本次临时 API Key。'}</span></div>
          {judgeBackend === 'api' && <label><span className="mb-1.5 block text-xs font-medium text-gray-600">模型 API Key</span><input type="password" autoComplete="off" className={inputClass} value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} placeholder="仅本次命令使用，不写入数据库" /></label>}
        </div>
        <label className="mt-4 block"><span className="mb-1.5 block text-xs font-medium text-gray-600">关注问题</span><textarea className={`${inputClass} min-h-24 resize-y`} value={focusIssue} maxLength={1000} onChange={(event) => onFocusIssueChange(event.target.value)} placeholder="例如：语雀 MCP 未调用、调用失败、参数错误，以及失败后给出未经验证的答案" /></label>
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs leading-5 text-gray-600"><span className="font-medium text-gray-700">实际诊断要求：</span>{diagnoseIntent}</div>
        <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs leading-5 text-blue-700">
          {dateRangeEnabled
            ? '完整流程将依次执行 Bot 诊断、进化规划和每轮优化；各节点的实际命令可在“自定义参数”中查看和调整。'
            : '诊断完成后将继续执行进化规划；各节点的实际命令可在“自定义参数”中查看和调整。'}
        </p>
      </section>
    </>
  )
}

function BenchFields({ domains, domainId, onDomainIdChange, error }: {
  domains: BenchDomain[]
  domainId: string
  onDomainIdChange: (value: string) => void
  error: string
}) {
  return (
    <section className="border-t border-gray-100 pt-6">
      <h2 className="text-sm font-semibold text-gray-900">Bench 范围</h2>
      <label className="mt-3 block">
        <span className="mb-1.5 block text-xs font-medium text-gray-600">Bench Domain <span className="text-red-500">*</span></span>
        <select className={inputClass} value={domainId} onChange={(event) => onDomainIdChange(event.target.value)}>
          <option value="">请选择包含已发布模板的 Domain</option>
          {domains.map((domain) => <option key={`${domain.ownerUserId}/${domain.domainId}`} value={domain.domainId}>{domain.name} · {domain.domainId}（{domain.templateCount} 个模板）</option>)}
        </select>
      </label>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {!error && domains.length === 0 && <p className="mt-2 text-xs text-amber-600">当前用户没有可用 Domain，请先到“进化评估”创建并发布模板。</p>}
      <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs leading-5 text-blue-700">任务将冻结所选 Domain 的已发布模板，并执行一次完整 Bench；实际节点命令可在“自定义参数”中查看和调整。</p>
    </section>
  )
}

function BenchOptimizeFields({ domains, trainDomainId, testDomainId, onTrainDomainIdChange, onTestDomainIdChange, error }: {
  domains: BenchDomain[]
  trainDomainId: string
  testDomainId: string
  onTrainDomainIdChange: (value: string) => void
  onTestDomainIdChange: (value: string) => void
  error: string
}) {
  const options = (label: string) => <><option value="">{label}</option>{domains.map((domain) => <option key={`${domain.ownerUserId}/${domain.domainId}`} value={domain.domainId}>{domain.name} · {domain.domainId}（{domain.templateCount} 个模板）</option>)}</>
  return <section className="border-t border-gray-100 pt-6">
    <h2 className="text-sm font-semibold text-gray-900">Bench 进化数据集</h2>
    <div className="mt-3 grid gap-4 sm:grid-cols-2">
      <label><span className="mb-1.5 block text-xs font-medium text-gray-600">训练 Domain <span className="text-red-500">*</span></span><select className={inputClass} value={trainDomainId} onChange={(event) => onTrainDomainIdChange(event.target.value)}>{options('请选择训练 Domain')}</select></label>
      <label><span className="mb-1.5 block text-xs font-medium text-gray-600">测试 Domain <span className="text-red-500">*</span></span><select className={inputClass} value={testDomainId} onChange={(event) => onTestDomainIdChange(event.target.value)}>{options('请选择测试 Domain')}</select></label>
    </div>
    <p className="mt-2 text-xs text-gray-500">优化阶段使用训练 Domain，验证阶段使用测试 Domain；两者允许选择同一个 Domain。模板版本会在任务创建时冻结。</p>
    {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    {!error && domains.length === 0 && <p className="mt-2 text-xs text-amber-600">当前用户没有可用 Domain，请先到“进化评估”创建并发布模板。</p>}
    <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs leading-5 text-blue-700">流程将依次执行 Baseline 与 Spec v0、每轮优化；两个节点的实际命令可在“自定义参数”中分别查看和调整。</p>
  </section>
}

function NodeCommandYamlFields({ values, onChange, definitions, expanded, onExpandedChange }: {
  values: Record<string, string>
  onChange: (value: Record<string, string>) => void
  definitions: NodeDefinition[]
  expanded: boolean
  onExpandedChange: (value: boolean) => void
}) {
  const defaults = Object.fromEntries(definitions.map((item) => [item.key, nodeYaml(item.defaultCommand)]))
  const labels = Object.fromEntries(definitions.map((item) => [item.key, item.label]))
  return (
    <section className="border-t border-gray-100 pt-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">节点命令参数</h2>
          <p className="mt-1 text-xs leading-5 text-gray-500">高级功能。默认使用系统命令；展开后可分别调整当前流程的每个投递节点。</p>
        </div>
        <button type="button" className={secondaryButton} onClick={() => {
          if (!expanded && Object.keys(values).length === 0) onChange({ ...defaults })
          onExpandedChange(!expanded)
        }}>{expanded ? '收起自定义参数' : '自定义参数'}</button>
      </div>
      {expanded && <div className="mt-4 space-y-4">
        {Object.entries(defaults).map(([node, defaultValue]) => <div key={node} className="rounded-xl border border-gray-200 p-4">
          <div className="flex items-center justify-between gap-3"><p className="text-sm font-medium text-gray-800">{labels[node] ?? node}</p><button type="button" className="text-xs font-medium text-blue-600" onClick={() => onChange({ ...values, [node]: defaultValue })}>恢复默认</button></div>
          <textarea className={`${inputClass} mt-3 min-h-28 resize-y font-mono text-xs leading-5`} value={values[node] ?? defaultValue} spellCheck={false} onChange={(event) => onChange({ ...values, [node]: event.target.value })} aria-label={`${labels[node] ?? node}节点 YAML`} />
        </div>)}
        <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-3 text-xs leading-5 text-blue-700">每个节点使用独立 YAML；Task、Step、Owner、Bench Domain 和 Round 由 ClawWeb 追加，不能在 YAML 中定义。</div>
      </div>}
    </section>
  )
}

function OptimizeFields({ botSelected, tasks, selectedTaskIds, onTaskIdsChange }: {
  botSelected: boolean; tasks: EvolveTask[]; selectedTaskIds: string[]; onTaskIdsChange: (ids: string[]) => void;
}) {
  const navigate = useNavigate()
  const primaryTaskId = selectedTaskIds[0] ?? ''
  return (
    <>
      <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">诊断输入</h2>
        <p className="mt-1 text-xs text-gray-500">创建任务时冻结选择；每轮自动加载所选任务的 Diagnose 和 Plan Output。</p>
        {!botSelected ? (
          <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-500">请先选择目标 Bot，系统将查找该 Bot 已完成规划的诊断任务。</div>
        ) : tasks.length === 0 ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
            <div><p className="text-xs font-medium text-amber-900">当前 Bot 没有可用于优化的诊断任务</p><p className="mt-1 text-[11px] leading-5 text-amber-700">请先完成 Bot 诊断和目标规划，再返回这里继续优化。</p></div>
            <button type="button" className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-100" onClick={() => navigate('/evolve/new?type=diagnose')}>创建 Bot 诊断</button>
          </div>
        ) : (
          <>
            <label className="mt-3 block"><span className="mb-1.5 block text-xs font-medium text-gray-600">主诊断任务 <span className="text-red-500">*</span></span><select className={inputClass} value={primaryTaskId} onChange={(event) => onTaskIdsChange(event.target.value ? [event.target.value, ...selectedTaskIds.slice(1).filter((id) => id !== event.target.value)] : [])}><option value="">请选择已完成 Plan 的诊断任务</option>{tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id}</option>)}</select></label>
            <label className="mt-3 block"><span className="mb-1.5 block text-xs font-medium text-gray-600">补充诊断任务（可选，可多选）</span><select multiple className={`${inputClass} min-h-20`} value={selectedTaskIds.slice(1)} onChange={(event) => onTaskIdsChange(primaryTaskId ? [primaryTaskId, ...Array.from(event.target.selectedOptions, (option) => option.value)] : [])}>{tasks.filter((task) => task.task_id !== primaryTaskId).map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id}</option>)}</select></label>
          </>
        )}
      </section>
      {botSelected && tasks.length > 0 && <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">Bench Domain</h2>
        <p className="mt-2 rounded-xl border border-blue-100 bg-blue-50/60 p-3 text-xs leading-5 text-blue-700">训练与测试 Bench Domain ID 从主诊断任务的 Plan Output 读取，并由 ClawWeb 作为系统参数下发。</p>
        <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs leading-5 text-blue-700">ClawWeb 将按轮次投递 Optimize 节点，并自动追加 Task、Step、Round 和 Bench Domain 等系统参数；实际节点命令可在“自定义参数”中查看和调整。</p>
        <p className="mt-2 text-xs text-gray-400">Round 1 完成后，由 ClawWeb 判断并发送下一轮；Bot 不自行发起 Round。</p>
      </section>}
    </>
  )
}

function PackRestoreFields({ packs, selectedPackId, onPackIdChange, loading, error }: {
  packs: Awaited<ReturnType<typeof api.evolve.listPacks>>['items']
  selectedPackId: string
  onPackIdChange: (value: string) => void
  loading: boolean
  error: string
}) {
  const selected = packs.find((pack) => pack.packId === selectedPackId)
  return (
    <section className="border-t border-gray-100 pt-6">
      <h2 className="text-sm font-semibold text-gray-900">Pack 版本</h2>
      <p className="mt-1 text-xs leading-5 text-gray-500">选择需要应用的已登记版本。恢复会修改目标 Bot 的可恢复环境内容。</p>
      <label className="mt-3 block">
        <span className="mb-1.5 block text-xs font-medium text-gray-600">目标版本 <span className="text-red-500">*</span></span>
        <select aria-label="目标 Pack 版本" className={inputClass} disabled={loading} value={selectedPackId} onChange={(event) => onPackIdChange(event.target.value)}>
          <option value="">{loading ? '正在加载 Pack 版本…' : '请选择 Pack 版本'}</option>
          {packs.map((pack) => <option key={pack.packId} value={pack.packId}>{pack.packId} · {pack.sourceKind}{pack.sourceRound ? ` R${pack.sourceRound}` : ''} · {pack.botId}</option>)}
        </select>
      </label>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {!loading && !error && packs.length === 0 && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">当前没有可应用的 Pack，请先创建 Pack 或完成一次进化。</p>}
      {selected && <div className="mt-3 grid gap-2 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 text-xs text-amber-900 sm:grid-cols-2"><p><span className="text-amber-700/70">来源 Bot：</span><span className="font-mono">{selected.botId}</span></p><p><span className="text-amber-700/70">来源任务：</span><span className="font-mono">{selected.taskId}</span></p><p><span className="text-amber-700/70">版本类型：</span>{selected.sourceKind}{selected.sourceRound ? ` / Round ${selected.sourceRound}` : ''}</p><p><span className="text-amber-700/70">摘要：</span><span className="font-mono">{selected.artifact.sha256?.slice(0, 16) ?? '—'}</span></p></div>}
      <div className="mt-3 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-xs leading-5 text-red-700">应用 Pack 会覆盖目标 Bot 的可恢复环境内容，请确认目标 Bot 和版本无误后再创建任务。</div>
    </section>
  )
}

function FullFlowFields({ mode, onModeChange, goal, onGoalChange }: {
  mode: FullInputMode;
  onModeChange: (value: FullInputMode) => void;
  goal: string;
  onGoalChange: (value: string) => void;
}) {
  return (
    <>
      <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">进化方式</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {([
            ['diagnose_goal', '先诊断再进化', '分析历史 Session，再结合目标生成 Bench 并优化。'],
            ['direct_goal', '按目标进化', '跳过 Session 诊断，根据一句话目标生成 Bench 并优化。'],
          ] as const).map(([value, title, description]) => (
            <button
              key={value}
              type="button"
              onClick={() => onModeChange(value)}
              className={`rounded-xl border p-4 text-left transition ${mode === value ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500/10' : 'border-gray-200 bg-white hover:border-blue-200'}`}
            >
              <span className={`block text-sm font-semibold ${mode === value ? 'text-blue-800' : 'text-gray-900'}`}>{title}</span>
              <span className="mt-1.5 block text-xs leading-5 text-gray-500">{description}</span>
            </button>
          ))}
        </div>
      </section>
      <section className="border-t border-gray-100 pt-6">
        <h2 className="text-sm font-semibold text-gray-900">优化目标</h2>
        <label className="mt-3 block">
          <span className="mb-1.5 block text-xs font-medium text-gray-600">一句话目标、成功标准和优先级 <span className="text-red-500">*</span></span>
          <textarea
            className={`${inputClass} min-h-28 resize-y`}
            value={goal}
            maxLength={2000}
            onChange={(event) => onGoalChange(event.target.value)}
            placeholder="例如：通过优化相关 Skill 和工具调用流程，使工具调用失败与异步任务未完成问题的任务完成率达到90%以上，优先解决诊断阶段识别出的高频根因。"
          />
          <span className="mt-1 block text-xs leading-5 text-gray-400">{mode === 'direct_goal'
            ? 'Plan 将根据该目标生成预期验证 Case、Spec 与 Bench Domain。'
            : 'Diagnose 提供事实和高频根因，Plan 将结合该目标生成 Spec 与 Bench Case。'}{goal.length}/2000</span>
        </label>
      </section>
    </>
  )
}

function TaskFormOverview({ taskType, fullInputMode, improvementSource }: {
  taskType: EvolveTask['task_type']
  fullInputMode: FullInputMode
  improvementSource: boolean
}) {
  const overview = (() => {
    if (taskType === 'full' && improvementSource) return {
      label: '治理优化',
      subtitle: '基于治理 Evidence 规划并优化',
      stages: [
        ['目标规划', '冻结治理证据，生成 Goal、Spec 与 Bench'],
        ['优化 Loop', '运行基线、Tune、验证与采纳'],
      ],
      deliverables: [
        ['Evidence', '治理证据快照'],
        ['Goal', '目标与成功标准'],
        ['Spec', '优化策略与约束'],
        ['Result', 'Diff、指标与版本'],
      ],
    }
    if (taskType === 'full') return fullInputMode === 'direct_goal' ? {
      label: '全流程',
      subtitle: '按目标进化',
      stages: [
        ['进化规划', '根据目标生成 Spec 与 Bench Case'],
        ['优化 Loop', '运行基线、Tune、验证与采纳'],
      ],
      deliverables: [
        ['Goal', '目标与成功标准'],
        ['Spec v0', '初始优化策略'],
        ['Bench', '训练与验证 Case'],
        ['Result', 'Diff、指标与版本'],
      ],
    } : {
      label: '全流程',
      subtitle: '先诊断再进化',
      stages: [
        ['Bot 诊断', '分析 Session，提取 Good/Bad Case'],
        ['进化规划', '结合诊断证据与目标生成 Spec'],
        ['优化 Loop', '运行基线、Tune、验证与采纳'],
      ],
      deliverables: [
        ['Case', '问题清单与诊断证据'],
        ['Goal', '目标与成功标准'],
        ['Spec & Bench', '策略与评估 Case'],
        ['Result', 'Diff、指标与版本'],
      ],
    }
    const definitions: Partial<Record<EvolveTask['task_type'], {
      label: string
      subtitle: string
      stages: string[][]
      deliverables: string[][]
    }>> = {
      diagnose: {
        label: '诊断',
        subtitle: '诊断并建立优化目标',
        stages: [
          ['Bot 诊断', '分析 Session，提取 Good/Bad Case'],
          ['目标规划', '生成 Goal、Spec 与 Bench Case'],
        ],
        deliverables: [
          ['问题清单', '高频根因与证据'],
          ['Good/Bad Case', '可复现评估样本'],
          ['Goal & Spec', '目标与初始策略'],
          ['Bench', '训练与验证 Case'],
        ],
      },
      optimize: {
        label: '优化',
        subtitle: '复用已有诊断与规划结果',
        stages: [['优化 Loop', '逐轮执行基线、Tune、验证与采纳']],
        deliverables: [
          ['Diff', '每轮有效变更'],
          ['运行指标', '优化与验证结果'],
          ['Spec', '按需迭代 Spec vN'],
          ['版本结果', '采纳或回退结论'],
        ],
      },
      bench: {
        label: '评测',
        subtitle: '使用已发布模板评测 Bot',
        stages: [['Bench 评测', '冻结模板并执行一次完整 Bench']],
        deliverables: [
          ['Bench Run', '可追踪的评测运行'],
          ['运行指标', '完成率与分项得分'],
          ['评测报告', 'Case 结果与问题分析'],
        ],
      },
      bench_optimize: {
        label: 'Bench 优化',
        subtitle: '使用训练与测试 Domain 驱动优化',
        stages: [
          ['Baseline 与 Spec v0', '冻结模板并建立优化前基线'],
          ['优化 Loop', '逐轮 Tune、独立验证与采纳'],
        ],
        deliverables: [
          ['Baseline', '训练与测试基线'],
          ['Spec v0', '初始优化策略'],
          ['Diff', '每轮有效变更'],
          ['Result', '指标与版本结果'],
        ],
      },
      pack: {
        label: '部署',
        subtitle: '保存当前 Bot 环境',
        stages: [['创建 Pack', '校验允许范围并生成可恢复版本']],
        deliverables: [
          ['环境快照', '可恢复内容清单'],
          ['Pack', '版本资产与校验信息'],
          ['上传结果', 'OSS 地址与摘要'],
        ],
      },
      pack_restore: {
        label: '部署',
        subtitle: '应用历史 Bot 环境版本',
        stages: [['恢复 Pack', '校验版本并应用到目标 Bot']],
        deliverables: [
          ['恢复结果', '应用状态与校验信息'],
          ['审计记录', '目标版本与执行日志'],
        ],
      },
      runtime_cleanup: {
        label: '应用部署',
        subtitle: '清理历史进化运行记录',
        stages: [['任务清理', '识别并删除带明确任务标记的历史 Agent 与 Session']],
        deliverables: [
          ['清理结果', '删除与跳过数量'],
          ['执行记录', 'Provider、命令与异常信息'],
        ],
      },
    }
    return definitions[taskType] ?? {
      label: '进化',
      subtitle: '执行当前任务流程',
      stages: [['任务执行', '按照任务配置完成对应节点']],
      deliverables: [['执行结果', '节点输出与运行记录']],
    }
  })()
  return (
    <EvolveTaskOverview {...overview} />
  )
}

function SummaryRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-4 py-1.5 text-xs"><span className="text-gray-500">{label}</span><span className={`min-w-0 break-all text-right font-medium text-gray-900 ${mono ? 'font-mono' : ''}`}>{value}</span></div>
}

function GovernanceSourceCard({ task }: { task: EvolveTask }) {
  if (!isGovernanceTask(task)) return null
  const improvementId = governanceImprovementId(task)
  const source = task.source
  return (
    <section className="mt-6 overflow-hidden rounded-2xl border border-indigo-100 bg-gradient-to-r from-indigo-50/80 via-white to-blue-50/70 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-5 p-5">
        <div className="flex min-w-0 items-start gap-4">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm"><Icon name="target" /></span>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">治理来源</p>
            <h2 className="mt-1 text-lg font-semibold text-gray-950">Insight Center 改进项{improvementId ? ` #${improvementId}` : ''}</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-gray-500">本任务依据该治理项中的问题描述和失败证据生成优化方案。</p>
          </div>
        </div>
        {improvementId && <NavLink to={`/insight?tab=improvements&improvementId=${improvementId}`} className="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-700">查看治理项 <Icon name="arrow" /></NavLink>}
      </div>
      <dl className="grid border-t border-indigo-100/80 bg-white/60 sm:grid-cols-2">
        <SourceMetric label="治理项" value={improvementId ? `#${improvementId}` : (source?.sourceId || '—')} />
        <SourceMetric label="失败证据" value={source?.evidenceCount == null ? '—' : `${source.evidenceCount} 条`} />
      </dl>
    </section>
  )
}

function SourceMetric({ label, value }: { label: string; value: string }) {
  return <div className="border-indigo-100/80 px-5 py-3.5 sm:border-r last:border-r-0"><dt className="text-[10px] font-medium uppercase tracking-wide text-gray-400">{label}</dt><dd className="mt-1 text-sm font-semibold text-gray-900">{value}</dd></div>
}

function taskVersionState(task: EvolveTask, versions: EvolveVersion[]) {
  const optimizeSteps = (task.steps ?? []).filter((step) => step.stepType === 'optimize')
  const completed = optimizeSteps.filter((step) => ['succeeded', 'completed'].includes(step.status))
  const stable = versions
    .filter((version) => version.taskId === task.task_id
      && version.kind === 'round'
      && version.acceptanceStatus === 'accepted'
      && Boolean(version.pack?.packId))
    .sort((left, right) => (right.round ?? 0) - (left.round ?? 0))[0]
  const hasRegisteredInitial = versions.some((version) => version.taskId === task.task_id
    && version.kind === 'initial' && Boolean(version.pack?.packId)) || Boolean(task.initialPack)
  const latest = completed.at(-1)
  const latestVersion = latest ? versions.find((version) => version.stepId === latest.stepId) : undefined
  const working = [...optimizeSteps].reverse().find((step) => ['created', 'dispatching', 'dispatched', 'running'].includes(step.status))
  return {
    stableLabel: stable?.round ? `R${stable.round}` : hasRegisteredInitial ? 'Initial' : '尚未建立',
    latestLabel: latest?.roundNo ? `R${latest.roundNo}` : null,
    latestStatus: latestVersion?.acceptanceStatus
      ?? (latest?.output?.benchDecision === 'passed' ? 'bench_passed_unconfirmed' : latest ? 'unknown' : null),
    workingLabel: working?.roundNo ? `R${working.roundNo} Candidate` : null,
  }
}

function TaskVersionStatus({ task, adminReadMode, canLoadVersions }: { task: EvolveTask; adminReadMode: boolean; canLoadVersions: boolean }) {
  const [versions, setVersions] = useState<EvolveVersion[]>([])
  const [loading, setLoading] = useState(canLoadVersions)
  const [loadError, setLoadError] = useState(canLoadVersions ? '' : '仅任务 Owner 或管理员可读取版本登记信息')
  const versionRefreshKey = (task.steps ?? [])
    .filter((step) => step.stepType === 'optimize')
    .map((step) => `${step.stepId}:${step.status}:${step.completedAt ?? ''}`)
    .join('|')
  useEffect(() => {
    if (!canLoadVersions) {
      setVersions([])
      setLoading(false)
      setLoadError('仅任务 Owner 或管理员可读取版本登记信息')
      return
    }
    let active = true
    setLoading(true)
    setLoadError('')
    void api.evolve.listVersions(task.bot_id, {
      scope: adminReadMode ? 'all' : 'mine',
      ownerUserId: adminReadMode ? task.user_id : undefined,
    }).then((result) => {
      if (active) setVersions(result.items.filter((version) => version.taskId === task.task_id))
    }).catch((error) => {
      if (active) {
        setVersions([])
        setLoadError(error instanceof Error ? error.message : '版本登记信息加载失败')
      }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [adminReadMode, canLoadVersions, task.bot_id, task.task_id, task.user_id, versionRefreshKey])
  const state = taskVersionState(task, versions)
  const latestStatusLabel = state.latestStatus === 'accepted'
    ? '已接受'
    : state.latestStatus === 'unregistered'
      ? 'Pack 未登记'
      : state.latestStatus === 'rejected'
        ? '未接受'
        : state.latestStatus === 'bench_passed_unconfirmed'
          ? 'Bench 通过，版本未确认'
          : '状态未知'
  return <section className="mt-6 overflow-hidden rounded-2xl border border-blue-100 bg-gradient-to-r from-blue-50 via-white to-violet-50 shadow-sm">
    <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wide text-blue-600">任务版本状态</p>
        <div className="mt-1 flex flex-wrap items-baseline gap-2">
          <h2 className="text-sm font-semibold text-gray-950">本任务当前稳定版本</h2>
          <span className="rounded-md bg-blue-600 px-2 py-1 text-xs font-semibold text-white">{loading ? '加载中…' : state.stableLabel}</span>
        </div>
        <p className="mt-1 text-xs text-gray-500">仅表示本任务内最后一次已接受并完成 Pack 的版本，不代表 Bot 的全局实时版本。</p>
        {loadError && <p className="mt-1 text-xs text-amber-700">{loadError}；未使用 Step Output 推断稳定版本。</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {state.latestLabel && <span className={`rounded-md border px-2.5 py-1.5 ${state.latestStatus === 'accepted' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : state.latestStatus === 'unregistered' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-white text-gray-600'}`}>最近评测 {state.latestLabel} · {latestStatusLabel}</span>}
        {state.workingLabel && <span className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 font-medium text-amber-700">正在评测 {state.workingLabel}</span>}
      </div>
    </div>
  </section>
}

function TaskDetail() {
  const { user } = useClientUser()
  const { enabled: adminReadMode } = useEvolveAdminScope()
  const navigate = useNavigate()
  const location = useLocation()
  const pathTaskId = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1) ?? '')
  const taskId = pathTaskId === 'runs' ? '' : pathTaskId
  const [task, setTask] = useState<EvolveTask | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [retryingStepId, setRetryingStepId] = useState('')
  const [cancelingStepId, setCancelingStepId] = useState('')
  const [retryError, setRetryError] = useState('')
  const [sharingBusy, setSharingBusy] = useState(false)
  const [logArchiveBusy, setLogArchiveBusy] = useState(false)
  const [logArchives, setLogArchives] = useState<EvolveTaskLogArchive[]>([])
  const loadTask = async () => {
    const data = await api.evolve.getTask(taskId)
    setTask(data)
  }
  const loadLogArchives = async () => {
    try {
      const data = await api.evolve.listTaskLogArchives(taskId)
      setLogArchives(data.items)
    } catch {
      setLogArchives([])
    }
  }
  useEffect(() => {
    let active = true
    if (!taskId) {
      queueMicrotask(() => {
        if (!active) return
        setTask(null)
        setLoadError('缺少任务 ID，请返回任务列表重新发起')
        setLoading(false)
      })
      return () => { active = false }
    }
    queueMicrotask(() => {
      if (!active) return
      setLoading(true)
      api.evolve.getTask(taskId)
        .then((data) => { if (active) setTask(data) })
        .catch((error) => { if (active) { const message = error instanceof Error ? error.message : '任务加载失败'; setLoadError(message.includes('TASK_NOT_SHARED') ? '权限不足，请联系任务 Owner 开启分享' : message) } })
        .finally(() => { if (active) setLoading(false) })
    })
    return () => { active = false }
  }, [taskId])
  useEffect(() => {
    if (!taskId) return
    void loadLogArchives()
  }, [taskId])
  const activeLogArchiveKey = logArchives
    ?.filter((item) => ['dispatching', 'running'].includes(item.status))
    .map((item) => item.archiveId).join(',') ?? ''
  useEffect(() => {
    if (!taskId || !activeLogArchiveKey) return undefined
    const timer = window.setInterval(() => {
      void loadLogArchives()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [taskId, activeLogArchiveKey])

  if (loading) return <div className="mx-auto max-w-5xl px-4 py-20 text-center text-sm text-gray-500">正在加载任务详情…</div>
  if (!task) return <div className="mx-auto max-w-5xl px-4 py-20 text-center"><p className="text-sm text-red-600">{loadError || '任务不存在'}</p><button onClick={() => navigate('/evolve')} className={`${secondaryButton} mt-4`}>返回任务列表</button></div>
  const steps = task.steps ?? []
  const view = statusView(task.status)
  const shared = task.config.shared === true
  const canShare = !adminReadMode && (user?.userId === task.created_by || user?.isClawEvolveAdmin === true)
  const canOperate = !adminReadMode && (user?.userId === task.user_id || canShare)
  const canRetryRecordedStep = (step: EvolveStep, index: number) => {
    if (!['failed', 'canceled'].includes(step.status)) return false
    if (index === steps.length - 1) return true
    return step.stepType === 'skill_init' && steps
      .filter((item) => item.stepId !== step.stepId)
      .every((item) => (item.status === 'created' && item.stepType !== 'skill_init')
        || (['succeeded', 'failed', 'canceled'].includes(item.status) && item.stepType === 'skill_init'))
  }
  const canCancelRecordedStep = (step: EvolveStep, index: number) => {
    if (!['created', 'dispatched', 'running'].includes(step.status)) return false
    const waitingForInitializer = step.status === 'created' && step.stepType !== 'skill_init'
      && steps.some((item) => item.stepType === 'skill_init'
        && !['succeeded', 'failed', 'canceled'].includes(item.status))
    if (waitingForInitializer) return false
    if (index === steps.length - 1) return true
    return step.stepType === 'skill_init' && steps
      .filter((item) => item.stepId !== step.stepId)
      .every((item) => item.status === 'created' && item.stepType !== 'skill_init')
  }
  const displayType = taskDisplayType(task)
  const baselinePack = task.initialPack?.status === 'available' && task.initialPack.artifact.ref
    ? task.initialPack
    : null
  const latestSuccessfulLogArchive = logArchives.find((item) => item.status === 'succeeded') ?? null
  const retryStep = async (step: EvolveStep) => {
    let apiKey: string | undefined
    const usesApiJudge = step.stepType === 'diagnose'
      && !/(?:^|\s)--judge[_-]backend(?:=|\s+)subagent(?:\s|$)/i.test(step.command)
    if (usesApiJudge) {
      apiKey = window.prompt('重新执行 Diagnose 需要再次输入 API Key')?.trim()
      if (!apiKey) return
    }
    setRetryingStepId(step.stepId)
    setRetryError('')
    try {
      await api.evolve.retryStep(task.task_id, step.stepId, apiKey)
      await loadTask()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : '继续执行失败')
    } finally {
      setRetryingStepId('')
    }
  }
  const cancelStep = async (step: EvolveStep) => {
    if (!window.confirm('确定停止当前节点吗？系统会终止 BaaS 进程，或向原 Message 会话发送停止指令；之后可以重新执行。')) return
    setCancelingStepId(step.stepId)
    setRetryError('')
    try {
      await api.evolve.cancelStep(task.task_id, step.stepId)
      await loadTask()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : '停止节点失败')
    } finally {
      setCancelingStepId('')
    }
  }
  const downloadBaselinePack = async () => {
    if (!baselinePack) return
    try {
      const download = await api.evolve.getPackDownloadUrl(task.task_id, baselinePack.stepId, 'baseline')
      const anchor = document.createElement('a'); anchor.href = download.url; anchor.download = download.filename
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
    } catch (error) { setRetryError(error instanceof Error ? error.message : '获取 Pack 下载地址失败') }
  }
  const toggleSharing = async () => {
    if (sharingBusy) return
    setSharingBusy(true)
    setRetryError('')
    try {
      await api.evolve.setTaskShared(task.task_id, !shared)
      await loadTask()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : '更新分享设置失败')
    } finally {
      setSharingBusy(false)
    }
  }
  const createLogArchive = async () => {
    setLogArchiveBusy(true)
    setRetryError('')
    try {
      await api.evolve.createTaskLogArchive(task.task_id)
      await loadLogArchives()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : '获取日志失败')
    } finally {
      setLogArchiveBusy(false)
    }
  }
  const downloadLogArchive = async (archiveId: string) => {
    try {
      const download = await api.evolve.getTaskLogArchiveDownloadUrl(task.task_id, archiveId)
      const anchor = document.createElement('a'); anchor.href = download.url; anchor.download = download.filename
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : '获取日志下载地址失败')
    }
  }

  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve')} className="mb-5 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800"><span className="rotate-180"><Icon name="arrow" /></span>返回任务列表</button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><Status type={view.type}>{view.text}</Status><TaskType type={displayType.key}>{displayType.label}</TaskType><span className="font-mono text-xs text-gray-400">{task.task_id}</span></div>
          <h1 className="mt-3 text-2xl font-semibold text-gray-950">{task.task_name || `${displayType.label}任务`}</h1>
          {task.remark && <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-gray-500">{task.remark}</p>}
          <div className="mt-3 flex items-center gap-3"><span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="bot" /></span><div><p className="text-sm font-medium text-gray-900">{task.bot_id}</p><p className="font-mono text-[11px] text-gray-400">{task.user_id} / {task.bot_id}{task.config.botEnv ? ` / ${String(task.config.botEnv)}` : ''}</p></div></div>
        </div>
      <div className="flex items-center gap-2">{shared && <span className="rounded-full bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700">已公开分享</span>}{canShare && <button type="button" className={secondaryButton} disabled={sharingBusy} onClick={() => void toggleSharing()}>{sharingBusy ? '更新中…' : shared ? '关闭分享' : '分享'}</button>}{latestSuccessfulLogArchive && <button type="button" className={secondaryButton} onClick={() => void downloadLogArchive(latestSuccessfulLogArchive.archiveId)}>下载最新日志</button>}{canOperate && <button type="button" className={secondaryButton} disabled={logArchiveBusy || logArchives.some((item) => ['dispatching', 'running'].includes(item.status))} onClick={() => void createLogArchive()}>{logArchiveBusy ? '正在发起…' : logArchives.some((item) => ['dispatching', 'running'].includes(item.status)) ? '日志获取中…' : latestSuccessfulLogArchive ? '重新获取日志' : '获取日志'}</button>}{baselinePack && <button type="button" className={secondaryButton} onClick={() => void downloadBaselinePack()}>下载初始 Pack</button>}{baselinePack && canOperate && <button type="button" className={secondaryButton} onClick={() => navigate(`/evolve/new?type=pack_restore&packId=${encodeURIComponent(baselinePack.packId)}&sourceTaskId=${encodeURIComponent(task.task_id)}&sourceKind=baseline&botEnv=${encodeURIComponent(String(task.config.botEnv ?? ''))}`)}>恢复到任务初始版本</button>}</div>
      </div>

      <GovernanceSourceCard task={task} />
      {['full', 'optimize', 'bench_optimize'].includes(task.task_type) && <TaskVersionStatus task={task} adminReadMode={adminReadMode} canLoadVersions={adminReadMode || user?.userId === task.user_id} />}

      <div className="mt-6 grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(260px,300px)]">
        <div className="min-w-0 space-y-5">
          <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-100 px-5 py-4">
              <div className="flex items-center justify-between gap-3">
                <div><h2 className="text-sm font-semibold text-gray-900">进化工作流</h2><p className="mt-1 text-xs text-gray-500">按目标设定、优化 Loop、结果应用组织 Bot 进化过程。</p></div>
                <Status type={view.type}>{view.text}</Status>
              </div>
            </div>
            <WorkflowNodes
              taskType={task.task_type}
              steps={steps}
              insightImprovement={isGovernanceTask(task)}
              inputMode={task.config.inputMode === 'direct_goal' ? 'direct_goal' : 'diagnose_goal'}
            />
          </section>
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div><h2 className="text-sm font-semibold text-gray-900">执行记录</h2><p className="mt-1 text-xs text-gray-500">命令、运行标识、输出和错误信息。</p></div>
              <span className="text-xs text-gray-400">{steps.length} 个 Step</span>
            </div>
            <div className="mt-5 space-y-3">
              {retryError && <p className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{retryError}</p>}
              {steps.map((step, index) => <StepCard key={step.stepId} step={step} canRetry={canOperate && canRetryRecordedStep(step, index)} canCancel={canOperate && canCancelRecordedStep(step, index)} retrying={retryingStepId === step.stepId} canceling={cancelingStepId === step.stepId} onRetry={() => void retryStep(step)} onCancel={() => void cancelStep(step)} />)}
              {steps.length === 0 && <div className="rounded-xl border border-dashed border-gray-200 py-10 text-center text-sm text-gray-400">尚未创建 Step</div>}
            </div>
          </section>
        </div>

        <aside className="min-w-0 space-y-4">
          {logArchives.length > 0 && <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div><h2 className="text-sm font-semibold text-gray-900">任务日志</h2><p className="mt-1 text-xs text-gray-500">独立归档，不影响任务节点和运行状态。</p></div>
            <div className="mt-4 space-y-3">
              {logArchives.map((archive, index) => <div key={archive.archiveId} className="rounded-xl border border-gray-100 bg-gray-50 p-3">
                <div className="flex items-center justify-between gap-2"><span className="font-mono text-[11px] text-gray-500">{archive.archiveId}{index === 0 ? ' · 最新' : ''}</span><span className={`text-xs font-medium ${archive.status === 'succeeded' ? 'text-emerald-700' : archive.status === 'failed' ? 'text-red-600' : 'text-blue-600'}`}>{archive.status === 'succeeded' ? '可下载' : archive.status === 'failed' ? '失败' : '处理中'}</span></div>
                <p className="mt-2 text-xs text-gray-400">{formatStepTime(archive.completedAt ?? archive.startedAt ?? archive.gmtCreate)}</p>
                {archive.error?.message && <p className="mt-2 break-words text-xs text-red-600">{archive.error.message}</p>}
                {archive.status === 'succeeded' && <button type="button" className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800" onClick={() => void downloadLogArchive(archive.archiveId)}>下载日志</button>}
              </div>)}
            </div>
          </section>}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900">任务信息</h2>
            <dl className="mt-4 space-y-2">
              <SummaryRow label="user_id" value={task.user_id} mono />
              <SummaryRow label="bot_id" value={task.bot_id} mono />
              <SummaryRow label="任务类型" value={displayType.label} />
              {governanceImprovementId(task) && <SummaryRow label="治理项" value={`#${governanceImprovementId(task)}`} />}
              <SummaryRow label="任务名称" value={task.task_name || '—'} />
              <SummaryRow label="备注" value={task.remark || '—'} />
              <SummaryRow label="Step 数" value={String(steps.length)} />
              <SummaryRow label="发起人" value={task.created_by} mono />
            </dl>
            {task.error_message && <p className="mt-4 rounded-lg bg-red-50 p-3 text-xs text-red-700">{task.error_message}</p>}
          </section>
          <TaskConfigPanel config={task.config} />
        </aside>
      </div>
    </div>
  )
}

type WorkflowPhase = {
  key: string
  title: string
  subtitle: string
  tone: 'blue' | 'green' | 'amber'
  nodes: Array<{ type: string; title: string; command: string; deliverable: string; matchTypes?: string[] }>
}

const targetPhase: WorkflowPhase = {
  key: 'target',
  title: '设定优化目标',
  subtitle: '诊断问题并形成可执行的优化方案',
  tone: 'blue',
  nodes: [
    { type: 'diagnose', title: 'Bot 诊断', command: 'clawevolve-diagnose', deliverable: '问题清单 · Good/Bad Case' },
    { type: 'plan', title: '目标规划', command: 'clawevolve-plan', deliverable: 'Goal · Spec v0 · Bench Case' },
  ],
}
const directGoalPhase: WorkflowPhase = {
  key: 'direct-goal',
  title: '按目标规划',
  subtitle: '根据一句话目标生成可执行的 Spec 与 Bench Case',
  tone: 'blue',
  nodes: [
    { type: 'plan', title: '目标规划', command: 'clawevolve-plan', deliverable: 'Goal · Spec v0 · Bench Case' },
  ],
}
const optimizePhase: WorkflowPhase = {
  key: 'loop',
  title: '实施优化 Loop',
  subtitle: 'ClawWeb 逐轮触发，每轮对应一个 Optimize Step',
  tone: 'green',
  nodes: [
    { type: 'optimize', matchTypes: ['optimize'], title: '优化 Loop', command: 'clawevolve-workflow --stage optimize', deliverable: '每轮 Diff · 指标 · Spec vN' },
  ],
}
const compactOptimizePhase: WorkflowPhase = {
  key: 'loop-compact',
  title: '实施优化 Loop',
  subtitle: '环境准备、Bench、优化、验证与复盘',
  tone: 'green',
  nodes: [
    {
      type: 'optimize-loop',
      matchTypes: ['optimize'],
      title: '优化 Loop',
      command: '由 ClawWeb 逐轮触发',
      deliverable: '有效 Diff · 指标 · Spec vN',
    },
  ],
}
const applyPhase: WorkflowPhase = {
  key: 'apply',
  title: '应用优化结果',
  subtitle: 'Review 后采纳并应用已验证的 Patch',
  tone: 'amber',
  nodes: [
    { type: 'apply', title: '应用 Patch', command: 'clawevolve-patch', deliverable: 'Patch · 应用结果 · 效果监控' },
  ],
}
const benchPhase: WorkflowPhase = {
  key: 'bench',
  title: '执行 Bench',
  subtitle: '按创建任务时冻结的已发布模板运行评测',
  tone: 'blue',
  nodes: [
    { type: 'bench', title: 'Bench 评测', command: 'clawevolve-bench', deliverable: '指标 · Run · 报告' },
  ],
}
const benchPlanPhase: WorkflowPhase = {
  key: 'bench-plan', title: 'Baseline 与 Spec v0', subtitle: '训练/测试基线、用户目标与初始优化策略', tone: 'blue',
  nodes: [{ type: 'bench_plan', title: 'Baseline 与 Spec v0', command: 'clawevolve-workflow --stage bench-plan', deliverable: 'Train/Test Baseline · Objective · Spec v0' }],
}
const insightPlanPhase: WorkflowPhase = {
  key: 'insight-plan', title: '进化规划', subtitle: '读取 Insight 改进项并形成优化输入', tone: 'blue',
  nodes: [{ type: 'plan', title: '进化规划', command: 'clawevolve-plan', deliverable: 'Goal · Spec v0 · Bench Case' }],
}

const workflowDefinitions: Partial<Record<EvolveTask['task_type'], WorkflowPhase[]>> = {
  diagnose: [targetPhase],
  optimize: [optimizePhase],
  apply: [applyPhase],
  full: [targetPhase, compactOptimizePhase],
  bench: [benchPhase],
  bench_optimize: [benchPlanPhase, optimizePhase],
  pack: [{ key: 'pack', title: '创建环境 Pack', subtitle: '保存当前 Bot 环境并发布到 OSS', tone: 'amber', nodes: [{ type: 'pack', title: '创建 Pack', command: 'clawevolve-pack --mode pack', deliverable: 'Pack · SHA-256 · OSS 引用' }] }],
  pack_restore: [{ key: 'restore', title: '恢复环境 Pack', subtitle: '将选择的历史 Pack 应用到 Bot', tone: 'amber', nodes: [{ type: 'restore', title: '恢复 Pack', command: 'clawevolve-pack --mode restore', deliverable: '恢复结果' }] }],
  runtime_cleanup: [{ key: 'runtime-cleanup', title: '任务清理', subtitle: '清理目标 Bot 草稿运行环境中的历史进化记录', tone: 'amber', nodes: [{ type: 'runtime_cleanup', title: '任务清理', command: 'clawevolve-runtime-cleanup', deliverable: '清理结果与计数' }] }],
}

function WorkflowNodes({ taskType, steps, insightImprovement = false, inputMode = 'diagnose_goal' }: {
  taskType: EvolveTask['task_type'];
  steps: EvolveStep[];
  insightImprovement?: boolean;
  inputMode?: FullInputMode;
}) {
  const phases = insightImprovement
    ? [insightPlanPhase, optimizePhase]
    : taskType === 'full' && inputMode === 'direct_goal'
      ? [directGoalPhase, compactOptimizePhase]
      : (workflowDefinitions[taskType] ?? [])
  const definitions = phases.flatMap((phase) => phase.nodes.map((node) => ({ ...node, tone: phase.tone })))
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
  const [optimizeLoopExpanded, setOptimizeLoopExpanded] = useState(
    () => steps.some((step) => step.stepType === 'optimize'),
  )
  useEffect(() => {
    if (selectedStepId && steps.some((step) => step.stepId === selectedStepId)) return
    const preferred = [...steps].reverse().find(
      (step) => !['succeeded', 'completed', 'failed', 'canceled'].includes(step.status),
    ) ?? steps.at(-1)
    queueMicrotask(() => setSelectedStepId(preferred?.stepId ?? null))
  }, [steps, selectedStepId])
  const selectedStep = steps.find((step) => step.stepId === selectedStepId)
  const initializationSteps = steps.filter((step) => step.stepType === 'skill_init')
  const hasOptimizeSteps = steps.some((step) => step.stepType === 'optimize')
  if (definitions.length === 0) {
    return <div className="mt-5 rounded-xl border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">暂无可展示的工作流定义</div>
  }
  return (
    <>
      <div className="overflow-x-auto px-5 py-6">
        <div className="flex min-w-max items-center">
          {definitions.map((definition, index) => {
            const matchingTypes = definition.matchTypes ?? [definition.type]
            const matching = steps.filter((step) => matchingTypes.includes(step.stepType))
            const latest = matching.at(-1)
            const completed = latest?.status === 'succeeded' || latest?.status === 'completed'
            const active = Boolean(latest && !completed && !['failed', 'canceled'].includes(latest.status))
            const view = active && definition.matchTypes
              ? { type: 'running' as const, text: '运行中' }
              : latest ? statusView(latest.status) : { type: 'scheduled' as const, text: '待执行' }
            const tone = {
              blue: { rail: 'bg-blue-50', border: 'border-blue-100', text: 'text-blue-700' },
              green: { rail: 'bg-emerald-50', border: 'border-emerald-100', text: 'text-emerald-700' },
              amber: { rail: 'bg-amber-50', border: 'border-amber-100', text: 'text-amber-700' },
            }[definition.tone]
            return (
              <div key={definition.type} className="flex items-center">
                <button
                  type="button"
                  disabled={!latest}
                  onClick={() => {
                    if (definition.matchTypes) {
                      setOptimizeLoopExpanded((expanded) => !expanded)
                      if (latest) setSelectedStepId(latest.stepId)
                      return
                    }
                    if (latest) setSelectedStepId(latest.stepId)
                  }}
                  className={`w-52 rounded-xl border p-4 text-left transition ${
                  completed ? 'border-emerald-200 bg-emerald-50/50'
                    : active ? 'border-blue-300 bg-blue-50/50 shadow-sm'
                      : latest?.status === 'failed' ? 'border-red-200 bg-red-50/50'
                        : `${tone.border} bg-white`
                  } ${latest ? 'cursor-pointer hover:-translate-y-0.5 hover:shadow-md' : 'cursor-default'} ${selectedStepId === latest?.stepId ? 'ring-2 ring-blue-500/20' : ''}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`flex h-7 w-7 items-center justify-center rounded-md ${completed ? 'bg-emerald-600 text-white' : active ? 'bg-blue-600 text-white' : `${tone.rail} ${tone.text}`}`}>
                      {completed ? <Icon name="check" /> : <span className="text-[11px] font-semibold">{index + 1}</span>}
                    </span>
                    <Status type={view.type}>{view.text}</Status>
                  </div>
                  <p className="mt-3 text-sm font-semibold text-gray-900">{definition.title}</p>
                  <p className={`mt-1 text-[9px] text-gray-400 ${definition.matchTypes ? '' : 'font-mono'}`}>{definition.matchTypes ? definition.command : `/${definition.command}`}</p>
                  <div className="mt-3 border-t border-gray-100 pt-2">
                    <p className="text-[9px] font-medium uppercase tracking-wide text-gray-400">交付物</p>
                    <p className="mt-1 text-[10px] leading-4 text-gray-600">{definition.deliverable}</p>
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-gray-100 pt-2 text-[9px] text-gray-400">
                    <span>{latest ? `创建 ${formatStepTime(latest.gmtCreate)}` : '等待上一步'}</span>
                    {latest && <span>{stepDuration(latest)}</span>}
                  </div>
                  {matching.length > 1 && <p className="mt-2 text-[10px] font-medium text-blue-600">{definition.matchTypes ? `${matching.length} 个子步骤 / 轮次` : `${matching.length} 轮 Step`}</p>}
                  {definition.matchTypes && latest && (
                    <p className="mt-2 flex items-center gap-1 text-[10px] font-medium text-blue-600">
                      <span className={`transition-transform ${optimizeLoopExpanded ? 'rotate-90' : ''}`}><Icon name="arrow" /></span>
                      {optimizeLoopExpanded ? '收起内部节点' : '展开内部节点'}
                    </p>
                  )}
                </button>
                {index < definitions.length - 1 && <WorkflowArrow completed={completed} />}
              </div>
            )
          })}
        </div>
      </div>
      {initializationSteps.length > 0 && (
        <div className="border-t border-blue-100 bg-blue-50/30 px-5 py-4">
          <div className="mb-3">
            <p className="text-xs font-semibold text-gray-900">内部节点</p>
            <p className="mt-1 text-[10px] text-gray-500">ARCA 业务节点只会在 Skill 同步与 OpenClaw 运行时准备完成后投递。</p>
          </div>
          <div className="flex flex-wrap gap-3">
            {initializationSteps.map((step, index) => {
              const view = statusView(step.status)
              return (
                <button
                  key={step.stepId}
                  type="button"
                  onClick={() => setSelectedStepId(step.stepId)}
                  className={`w-52 rounded-lg border bg-white p-3 text-left transition hover:border-blue-300 hover:shadow-sm ${selectedStepId === step.stepId ? 'border-blue-400 ring-2 ring-blue-500/10' : 'border-blue-100'}`}
                >
                  <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-gray-900">Skill 初始化{initializationSteps.length > 1 ? ` · 重试 ${index + 1}` : ''}</span><Status type={view.type}>{view.text}</Status></div>
                  <p className="mt-2 text-[10px] text-gray-500">同步 Release · 清理历史会话 · 重启 Gateway</p>
                  <p className="mt-2 font-mono text-[9px] text-gray-400">{step.stepId}</p>
                </button>
              )
            })}
          </div>
        </div>
      )}
      {['full', 'optimize', 'bench_optimize'].includes(taskType) && hasOptimizeSteps && optimizeLoopExpanded && (
        <OptimizeLoopDetails
          steps={steps}
          selectedStepId={selectedStepId}
          onSelect={setSelectedStepId}
          inputMode={inputMode}
        />
      )}
      {selectedStep && <WorkflowNodeInspector step={selectedStep} />}
    </>
  )
}

function OptimizeLoopDetails({
  steps,
  selectedStepId,
  onSelect,
  inputMode,
}: {
  steps: EvolveStep[]
  selectedStepId: string | null
  onSelect: (stepId: string) => void
  inputMode: FullInputMode
}) {
  const rounds = steps
    .filter((step) => step.stepType === 'optimize')
    .sort((left, right) => (left.roundNo ?? left.stepNo) - (right.roundNo ?? right.stepNo))
  return (
    <div className="border-t border-emerald-100 bg-emerald-50/30 px-5 py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-gray-900">优化轮次</p>
          <p className="mt-1 text-[10px] text-gray-500">每轮由 ClawWeb 独立创建并投递一个 Optimize Step。</p>
        </div>
      </div>
      <div className="overflow-x-auto pb-1">
        <div className="flex min-w-max items-center">
          {rounds.map((step, index) => {
            const view = statusView(step.status)
            return (
              <div key={step.stepId} className="flex items-center">
                <button
                  type="button"
                  onClick={() => onSelect(step.stepId)}
                  className={`w-44 cursor-pointer rounded-lg border bg-white p-3 text-left transition hover:border-emerald-300 hover:shadow-sm ${
                    selectedStepId === step.stepId ? 'border-emerald-400 ring-2 ring-emerald-500/10' : 'border-emerald-100'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex h-6 min-w-6 items-center justify-center rounded-md bg-emerald-50 px-1.5 text-[10px] font-semibold text-emerald-700">R{step.roundNo ?? index + 1}</span>
                    <Status type={view.type}>{view.text}</Status>
                  </div>
                  <p className="mt-2 text-xs font-semibold text-gray-900">第 {step.roundNo ?? index + 1} 轮优化</p>
                  <p className="mt-1 text-[9px] leading-4 text-gray-500">Diff · 运行指标 · 可选 Spec</p>
                  <div className="mt-2 border-t border-gray-100 pt-2 text-[9px] text-gray-400">
                    {formatStepTime(step.startedAt ?? step.gmtCreate)} · {stepDuration(step)}
                  </div>
                </button>
                {index < rounds.length - 1 && <WorkflowArrow completed={['succeeded', 'completed'].includes(step.status)} />}
              </div>
            )
          })}
          {rounds.length === 0 && (
            <div className="w-full rounded-lg border border-dashed border-emerald-200 bg-white/70 px-4 py-6 text-center text-xs text-gray-400">
              {inputMode === 'direct_goal' ? '目标规划完成后' : '诊断输入就绪后'}，ClawWeb 将创建并发送 Round 1
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function formatStepTime(value: number | string | null | undefined): string {
  if (value == null) return '—'
  const date = typeof value === 'number'
    ? new Date(value < 10_000_000_000 ? value * 1000 : value)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

function stepDuration(step: EvolveStep): string {
  const start = step.startedAt ?? (typeof step.gmtCreate === 'number' ? step.gmtCreate : Math.floor(new Date(step.gmtCreate).getTime() / 1000))
  if (!start || Number.isNaN(start)) return '等待启动'
  const end = step.completedAt ?? Math.floor(Date.now() / 1000)
  const seconds = Math.max(0, end - start)
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function WorkflowNodeInspector({ step }: { step: EvolveStep }) {
  const view = statusView(step.status)
  const dispatchLabel = stepDispatchLabel(step)
  return (
    <div className="border-t border-gray-100 bg-gray-50/70 px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><p className="text-xs font-semibold text-gray-900">节点运行详情</p><Status type={view.type}>{view.text}</Status>{dispatchLabel && <span className="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-medium text-sky-700">{dispatchLabel}</span>}</div>
          <p className="mt-1 font-mono text-[10px] text-gray-400">{step.stepId}</p>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-gray-500">
          <span>创建：{formatStepTime(step.gmtCreate)}</span>
          <span>启动：{formatStepTime(step.startedAt)}</span>
          <span>结束：{formatStepTime(step.completedAt)}</span>
          {step.roundNo != null && <span>第 {step.roundNo} 轮</span>}
        </div>
      </div>
      {step.summary && <p className="mt-3 text-xs text-gray-700">{step.summary}</p>}
      {step.error && <p className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-700">{step.error.code ? `${step.error.code}: ` : ''}{step.error.message}</p>}
      {step.output && <StepDeliverables output={step.output} taskId={step.taskId} stepId={step.stepId} />}
      <details className="mt-3">
        <summary className="cursor-pointer text-[11px] font-medium text-blue-600">查看命令与运行标识</summary>
        <div className="mt-2 grid gap-2 rounded-lg border border-gray-200 bg-white p-3 text-[10px] sm:grid-cols-2">
          <div><p className="text-gray-400">Bot Run ID</p><p className="mt-1 break-all font-mono text-gray-700">{step.botRunId ?? '—'}</p></div>
          <div><p className="text-gray-400">Bot Session ID</p><p className="mt-1 break-all font-mono text-gray-700">{step.botSessionId ?? '—'}</p></div>
          <div className="sm:col-span-2"><p className="text-gray-400">Command</p><p className="mt-1 break-all font-mono leading-5 text-gray-700">{step.command}</p></div>
        </div>
      </details>
    </div>
  )
}

function WorkflowArrow({ completed }: { completed: boolean }) {
  return (
    <div className={`relative mx-2 h-px w-8 ${completed ? 'bg-emerald-400' : 'bg-gray-200'}`}>
      <span className={`absolute -right-0.5 -top-1 h-2 w-2 rotate-45 border-r border-t ${completed ? 'border-emerald-400' : 'border-gray-300'}`} />
    </div>
  )
}

function TaskConfigPanel({ config }: { config: Record<string, unknown> }) {
  const sessionSource = config.sessionSource && typeof config.sessionSource === 'object'
    ? (config.sessionSource as { mode?: unknown }).mode
    : undefined
  const rows = [
    ['进化方式', config.inputMode === 'direct_goal' ? '按目标进化' : config.inputMode === 'diagnose_goal' ? '先诊断再进化' : undefined],
    ['Session 来源', sessionSource === 'service_export' ? '服务 Session（只读导出）' : sessionSource === 'local' ? '个人 Bot 本地 Session' : undefined],
    ['优化目标', config.goal],
    ['诊断模型', config.model],
    ['最多诊断 Session', config.maxSessions],
    ['诊断要求', config.diagnoseIntent],
    ['Bot 阶段', config.lifecycleStage === 'draft' ? '草稿' : config.lifecycleStage],
    ['命令投递', config.forceMessage === true ? '强制 Bot Message' : '按 Bot provider 自动选择'],
  ].filter((row): row is [string, string | number] => row[1] !== null && row[1] !== undefined)
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-gray-900">任务配置</h2>
      <div className="mt-4 divide-y divide-gray-100">
        {rows.map(([label, value]) => <div key={label} className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 py-3 text-xs"><span className="text-gray-500">{label}</span><span className="min-w-0 break-all text-right font-medium text-gray-900">{String(value)}</span></div>)}
      </div>
      <details className="mt-3 border-t border-gray-100 pt-3">
        <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">查看 YAML 与原始配置</summary>
        {Boolean(config.nodeCommands && typeof config.nodeCommands === 'object') && <pre className="mt-3 max-w-full overflow-auto whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-3 text-[10px] leading-5 text-gray-700">{JSON.stringify(config.nodeCommands, null, 2)}</pre>}
        <pre className="mt-3 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-3 text-[10px] leading-5 text-gray-200">{JSON.stringify(config, null, 2)}</pre>
      </details>
    </section>
  )
}

function StepCard({ step, canRetry = false, canCancel = false, retrying = false, canceling = false, onRetry, onCancel }: { step: EvolveStep; canRetry?: boolean; canCancel?: boolean; retrying?: boolean; canceling?: boolean; onRetry?: () => void; onCancel?: () => void }) {
  const view = statusView(step.status)
  const dispatchLabel = stepDispatchLabel(step)
  const stepLabel: Record<string, string> = {
    skill_init: 'Skill 初始化', diagnose: 'Bot 诊断', plan: '目标规划', envprep: '环境准备', bench: '基线评测',
    optimize: '策略优化', test: '验证评测', review: '轮次复盘', apply: '应用 Patch',
  }
  return (
    <div className="rounded-xl border border-gray-200 p-4 transition hover:border-gray-300">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><div className="flex items-center gap-2"><p className="text-sm font-semibold text-gray-900">{stepLabel[step.stepType] ?? step.stepType}</p>{step.roundNo != null && <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">第 {step.roundNo} 轮</span>}<Status type={view.type}>{view.text}</Status>{dispatchLabel && <span className="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-medium text-sky-700">{dispatchLabel}</span>}</div><p className="mt-1 font-mono text-[10px] text-gray-400">{step.stepId}</p></div>
        <div className="flex items-center gap-3">{step.botRunId && <span className="font-mono text-[10px] text-gray-400">{step.botRunId}</span>}{canCancel && <button type="button" disabled={canceling} onClick={onCancel} className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50">{canceling ? '正在停止…' : '停止'}</button>}{canRetry && <button type="button" disabled={retrying} onClick={onRetry} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">{retrying ? '正在继续…' : '继续执行'}</button>}</div>
      </div>
      {step.summary && <p className="mt-3 text-sm text-gray-700">{step.summary}</p>}
      {step.error && <p className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-700">{step.error.code ? `${step.error.code}: ` : ''}{step.error.message}</p>}
      {step.output && <StepDeliverables output={step.output} taskId={step.taskId} stepId={step.stepId} />}
      <details className="mt-3 border-t border-gray-100 pt-3">
        <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">技术信息</summary>
        <div className="mt-2 rounded-lg bg-gray-50 px-3 py-2 font-mono text-[10px] leading-5 text-gray-600">{step.command}</div>
        {step.botResponse && <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-gray-950 p-3 text-[10px] leading-5 text-gray-200">{JSON.stringify(step.botResponse, null, 2)}</pre>}
        {step.output && <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-gray-950 p-3 text-[10px] leading-5 text-gray-200">{JSON.stringify(step.output, null, 2)}</pre>}
      </details>
    </div>
  )
}

function StepDeliverables({ output, taskId, stepId }: { output: Record<string, unknown>; taskId: string; stepId: string }) {
  const initialization = output.schemaVersion === 'clawevolve.skill-init.v1' ? {
    result: String(output.result ?? ''),
    releaseVersion: String(output.releaseVersion ?? ''),
    transport: String(output.transport ?? ''),
    user: String(output.user ?? ''),
  } : undefined
  const diagnosis = output.diagnosis as { summary?: string; issues?: unknown[] } | undefined
  const cases = output.cases as {
    total?: number
    goodCount?: number
    badCount?: number
    items?: Array<{
      caseId?: string
      type?: 'good' | 'bad' | string
      summary?: string
    }>
  } | undefined
  const goal = output.goal as {
    title?: string
    summary?: string
    metrics?: Array<{
      key?: string
      name?: string
      operator?: string
      target?: string | number
      unit?: string
    }>
  } | string | undefined
  const primaryGoalMetric = typeof goal === 'object' ? goal.metrics?.[0] : undefined
  const benchCases = output.benchCases as {
    trainCount?: number
    testCount?: number
    items?: Array<{
      sourceCaseId?: string
      taskId?: string
      split?: 'train' | 'test' | string
      template?: { ownerUserId?: string; domainId?: string; templateName?: string; version?: number }
    }>
  } | undefined
  const benchDomains = output.benchDomains as {
    trainBenchDomainId?: string
    testBenchDomainId?: string
  } | undefined
  type BaselineResult = {
    role?: 'train' | 'test' | string
    producerStepId?: string
    source?: 'generated' | 'reused' | string
    cacheStatus?: string
    ownerUserId?: string
    domainId?: string
    benchRunId?: string
    metrics?: { score?: number; maxScore?: number; passRate?: number; caseCount?: number }
  }
  const baseline = output.baseline as { train?: BaselineResult; test?: BaselineResult } | undefined
  const benchResult = typeof output.benchRunId === 'string' ? {
    benchRunId: output.benchRunId,
    domainId: typeof output.domainId === 'string' ? output.domainId : undefined,
    metrics: output.metrics as BaselineResult['metrics'] | undefined,
  } : undefined
  const diff = output.diff as {
    summary?: string
    files?: Array<{ path?: string; change?: string }>
    artifact?: { ref?: string }
  } | undefined
  const pack = output.pack as { status?: string; artifact?: { ref?: string } } | undefined
  const [diffContent, setDiffContent] = useState<string | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState('')
  const [specExpanded, setSpecExpanded] = useState(false)
  const loadDiff = async () => {
    if (!taskId || !stepId) return
    setDiffLoading(true); setDiffError('')
    try { setDiffContent(await api.evolve.getStepDiff(taskId, stepId)) }
    catch (error) { setDiffError(error instanceof Error ? error.message : String(error)) }
    finally { setDiffLoading(false) }
  }
  const runMetrics = Array.isArray(output.metrics) ? output.metrics as Array<{
    key?: string
    name?: string
    value?: string | number
    unit?: string
    target?: string | number
    passed?: boolean
    benchRunId?: string
    baselineValue?: number
    delta?: number
    ownerUserId?: string
    domainId?: string
  }> : undefined
  const benchDecision = typeof output.benchDecision === 'string'
    ? output.benchDecision
    : undefined
  const reviewStatus = typeof output.reviewStatus === 'string'
    ? output.reviewStatus
    : undefined
  const scoreComparison = output.scoreComparison as {
    name?: string
    baseline?: number | null
    candidate?: number | null
    delta?: number | null
  } | undefined
  const roundDecision = output.roundDecision as { stop?: boolean; reason?: string } | undefined
  const spec = (output.spec ?? output.specV0) as {
    version?: string
    content_type?: 'text' | 'json'
    content?: string | Record<string, unknown>
  } | undefined
  const specContent = spec
    ? typeof spec.content === 'string'
      ? spec.content
      : JSON.stringify(spec.content ?? {}, null, 2)
    : ''
  const specLineCount = specContent ? specContent.split('\n').length : 0
  const specCollapsible = specLineCount > 12 || specContent.length > 1200
  const items = [
    initialization && { label: 'Release', value: initialization.releaseVersion, tone: 'blue' },
    initialization && { label: '初始化结果', value: initialization.result, tone: 'emerald' },
    initialization && { label: '执行方式', value: `${initialization.transport} / ${initialization.user}`, tone: 'violet' },
    diagnosis && { label: '诊断结论', value: diagnosis.summary ?? `${diagnosis.issues?.length ?? 0} 个问题`, tone: 'violet' },
    cases && { label: 'Bench Case', value: `${cases.total ?? 0} 个 · Good ${cases.goodCount ?? 0} / Bad ${cases.badCount ?? 0}`, tone: 'blue' },
    goal && { label: 'Goal', value: typeof goal === 'string' ? goal : goal.title ?? goal.summary ?? '已生成', tone: 'emerald' },
  ].filter(Boolean) as Array<{ label: string; value: string; tone: string }>
  if (items.length === 0 && !spec && !benchCases && !baseline && !benchResult && !diff && !runMetrics && !roundDecision && !benchDecision && !reviewStatus && !scoreComparison) return null
  const toneClass: Record<string, string> = {
    violet: 'border-violet-100 bg-violet-50 text-violet-700',
    blue: 'border-blue-100 bg-blue-50 text-blue-700',
    emerald: 'border-emerald-100 bg-emerald-50 text-emerald-700',
    amber: 'border-amber-100 bg-amber-50 text-amber-700',
  }
  return (
    <div className="mt-3 space-y-2">
      {items.length > 0 && <div className="grid gap-2 sm:grid-cols-2">
        {items.map((item) => <div key={item.label} className={`rounded-lg border px-3 py-2.5 ${toneClass[item.tone]}`}><p className="text-[10px] font-medium uppercase tracking-wide opacity-70">{item.label}</p><p className="mt-1 line-clamp-2 text-xs font-medium">{item.value}</p></div>)}
      </div>}
      {primaryGoalMetric && <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-3 text-emerald-900">
        <p className="text-[10px] font-medium uppercase tracking-wide text-emerald-700/70">目标指标</p>
        <div className="mt-2"><div className="flex items-center justify-between gap-3 rounded-md bg-white/70 px-2.5 py-2">
          <div className="min-w-0"><p className="truncate text-[11px] font-medium">{primaryGoalMetric.name ?? primaryGoalMetric.key ?? '主指标'}</p>{primaryGoalMetric.key && <p className="mt-0.5 truncate font-mono text-[9px] text-emerald-700/60">{primaryGoalMetric.key}</p>}</div>
          <span className="shrink-0 font-mono text-xs font-semibold">{primaryGoalMetric.operator ?? ''} {String(primaryGoalMetric.target ?? '—')}{primaryGoalMetric.unit ? ` ${primaryGoalMetric.unit}` : ''}</span>
        </div></div>
      </div>}
      {diff && <div className="rounded-lg border border-sky-100 bg-sky-50 px-3 py-3 text-sky-900">
        <p className="text-[10px] font-medium uppercase tracking-wide text-sky-700/70">本轮 Diff</p>
        <p className="mt-2 text-xs font-medium">{diff.summary ?? '已生成优化变更'}</p>
        {diff.files && diff.files.length > 0 && <div className="mt-2 divide-y divide-sky-100 rounded-md bg-white/70 px-2.5">
          {diff.files.map((file, index) => <div key={`${file.path}-${index}`} className="py-2">
            <p className="break-all font-mono text-[10px] text-sky-800">{file.path ?? `File ${index + 1}`}</p>
            {file.change && <p className="mt-1 text-[10px] leading-4 text-sky-900/70">{file.change}</p>}
          </div>)}
        </div>}
        {diff.artifact?.ref && <div className="mt-2">
          <div className="flex items-center gap-2"><button type="button" onClick={loadDiff} disabled={diffLoading} className="rounded-md border border-sky-200 bg-white px-2.5 py-1.5 text-[10px] font-medium text-sky-700 hover:bg-sky-50 disabled:opacity-50">{diffLoading ? '读取中…' : diffContent == null ? '查看完整 Diff' : '刷新 Diff'}</button>{diffContent != null && <button type="button" onClick={() => { const url = URL.createObjectURL(new Blob([diffContent], { type: 'text/x-diff;charset=utf-8' })); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${taskId}-${stepId}.diff`; anchor.click(); URL.revokeObjectURL(url) }} className="rounded-md border border-sky-200 bg-white px-2.5 py-1.5 text-[10px] font-medium text-sky-700 hover:bg-sky-50">下载 Diff</button>}</div>
          {diffError && <p className="mt-2 text-[10px] text-red-600">{diffError}</p>}
          {diffContent != null && <GitDiffView content={diffContent} />}
        </div>}
      </div>}
      {pack?.status === 'available' && pack.artifact?.ref && <div className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-3 text-amber-900"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-[10px] font-medium uppercase tracking-wide text-amber-700/70">本轮 Pack</p><p className="mt-1 text-xs">已生成并发布可恢复的环境版本</p></div><button type="button" className="rounded-md border border-amber-200 bg-white px-2.5 py-1.5 text-[10px] font-medium text-amber-700 hover:bg-amber-50" onClick={async () => { const download = await api.evolve.getPackDownloadUrl(taskId, stepId, 'round'); const anchor = document.createElement('a'); anchor.href = download.url; anchor.download = download.filename; document.body.appendChild(anchor); anchor.click(); anchor.remove() }}>下载 Pack</button></div></div>}
      {benchResult && <div className="rounded-lg border border-cyan-100 bg-cyan-50 px-3 py-3 text-cyan-950">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-[10px] font-medium uppercase tracking-wide text-cyan-700/70">Bench 结果</p><p className="mt-1 font-mono text-sm font-semibold">{benchResult.metrics?.score ?? '—'} / {benchResult.metrics?.maxScore ?? '—'}</p><p className="mt-1 text-[10px] text-cyan-800/70">{benchResult.metrics?.caseCount ?? '—'} 个 Case · Pass Rate {benchResult.metrics?.passRate ?? '—'}{benchResult.domainId ? ` · ${benchResult.domainId}` : ''}</p></div><a href={`/evolve/bench/runs/${encodeURIComponent(benchResult.benchRunId)}`} target="_blank" rel="noopener noreferrer" className="rounded-md border border-cyan-200 bg-white px-2.5 py-1.5 text-[10px] font-medium text-blue-600 hover:bg-cyan-50">查看 Bench Run ↗</a></div>
      </div>}
      {baseline && <div className="rounded-lg border border-cyan-100 bg-cyan-50 px-3 py-3 text-cyan-950">
        <p className="text-[10px] font-medium uppercase tracking-wide text-cyan-700/70">Train / Test Baseline</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {(['train', 'test'] as const).map((role) => {
            const result = baseline[role]
            if (!result) return null
            const producedHere = result.producerStepId === stepId
            return <div key={role} className="rounded-md bg-white/80 px-2.5 py-2.5">
              <div className="flex items-center justify-between gap-2"><span className="text-[10px] font-semibold uppercase text-cyan-800">{role}</span><span className="rounded bg-cyan-50 px-1.5 py-0.5 text-[9px] text-cyan-700">{producedHere ? '本节点生成' : '复用基线'} · {result.cacheStatus || result.source || '—'}</span></div>
              {producedHere ? <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]"><div><span className="text-cyan-700/70">Score</span><p className="font-mono text-sm font-semibold">{result.metrics?.score ?? '—'}</p></div><div><span className="text-cyan-700/70">Pass Rate</span><p className="font-mono text-sm font-semibold">{result.metrics?.passRate ?? '—'}</p></div><div><span className="text-cyan-700/70">Case</span><p className="font-mono">{result.metrics?.caseCount ?? '—'}</p></div><div><span className="text-cyan-700/70">Domain</span><p className="truncate font-mono" title={result.domainId}>{result.domainId || '—'}</p></div></div> : <p className="mt-2 break-all font-mono text-[9px] text-cyan-700">来源 Step：{result.producerStepId || '未知'}</p>}
              {result.benchRunId && <a href={`/evolve/bench/runs/${encodeURIComponent(result.benchRunId)}`} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex text-[10px] font-medium text-blue-600 hover:text-blue-700">查看 Bench Run ↗</a>}
            </div>
          })}
        </div>
      </div>}
      {runMetrics && runMetrics.length > 0 && <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-3 text-emerald-900">
        <p className="text-[10px] font-medium uppercase tracking-wide text-emerald-700/70">本轮运行指标</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {runMetrics.map((metric, index) => <div key={`${metric.key}-${metric.benchRunId}-${index}`} className="rounded-md bg-white/70 px-2.5 py-2">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0"><p className="truncate text-[11px] font-medium">{metric.name ?? metric.key ?? `指标 ${index + 1}`}</p>{metric.key && <p className="mt-0.5 truncate font-mono text-[9px] text-emerald-700/60">{metric.key}</p>}</div>
              <div className="shrink-0 text-right"><p className="font-mono text-sm font-semibold">{String(metric.value ?? '—')}{metric.unit ? ` ${metric.unit}` : ''}</p>{metric.target != null && <p className="mt-0.5 text-[9px] text-emerald-700/60">目标 {String(metric.target)}</p>}{metric.baselineValue != null && <p className="mt-0.5 text-[9px] text-emerald-700/60">基线 {metric.baselineValue} · Δ {metric.delta == null ? '—' : metric.delta >= 0 ? `+${metric.delta}` : metric.delta}</p>}</div>
            </div>
            <div className="mt-2 flex items-center justify-between gap-2">
              {metric.passed == null ? <span /> : <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${metric.passed ? 'bg-emerald-100 text-emerald-700' : 'bg-red-50 text-red-700'}`}>{metric.passed ? '已达标' : '未达标'}</span>}
              {metric.benchRunId && <a href={`/evolve/bench/runs/${encodeURIComponent(metric.benchRunId)}`} target="_blank" rel="noopener noreferrer" className="font-mono text-[9px] text-blue-600 hover:text-blue-700">查看 Bench Run ↗</a>}
            </div>
          </div>)}
        </div>
      </div>}
      {spec && <div className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-3 text-amber-800">
        <div className="flex items-center justify-between gap-3">
          <div><p className="text-[10px] font-medium uppercase tracking-wide opacity-70">{runMetrics ? '更新后的 ' : ''}Spec {spec.version ?? '—'}</p>{specLineCount > 0 && <p className="mt-0.5 text-[9px] text-amber-700/60">{specLineCount} 行{specCollapsible ? ' · 默认折叠预览' : ''}</p>}</div>
          <span className="rounded bg-white/70 px-1.5 py-0.5 font-mono text-[9px] uppercase">{spec.content_type ?? 'legacy'}</span>
        </div>
        <div className="relative mt-2 overflow-hidden rounded-md bg-white/70">
          <pre className={`overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[10px] leading-5 text-amber-950 transition-[max-height] ${specExpanded || !specCollapsible ? 'max-h-[720px]' : 'max-h-52'}`}>{specContent}</pre>
          {specCollapsible && !specExpanded && <div className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-amber-50 via-amber-50/90 to-transparent" />}
        </div>
        {specCollapsible && <button type="button" onClick={() => setSpecExpanded((value) => !value)} className="mt-2 inline-flex items-center gap-1 text-[10px] font-medium text-amber-700 hover:text-amber-900" aria-expanded={specExpanded}>{specExpanded ? '收起 Spec' : '展开完整 Spec'} <span aria-hidden="true">{specExpanded ? '↑' : '↓'}</span></button>}
      </div>}
      {(benchDecision || reviewStatus || scoreComparison) && <div className="rounded-lg border border-violet-100 bg-violet-50 px-3 py-3 text-violet-950">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-violet-700/70">Bench / Review</p>
          <div className="flex flex-wrap gap-1.5 text-[9px] font-medium">
            {benchDecision && <span className={`rounded px-1.5 py-0.5 ${benchDecision === 'passed' ? 'bg-emerald-100 text-emerald-700' : 'bg-white/80 text-violet-700'}`}>Bench：{benchDecision}</span>}
            {reviewStatus && <span className="rounded bg-white/80 px-1.5 py-0.5 text-violet-700">Review：{reviewStatus}</span>}
          </div>
        </div>
        {scoreComparison && <div className="mt-2 grid grid-cols-3 gap-2 text-[10px]">
          <div><span className="text-violet-700/70">Test baseline</span><p className="font-mono text-sm font-semibold">{scoreComparison.baseline ?? '—'}</p></div>
          <div><span className="text-violet-700/70">Test candidate</span><p className="font-mono text-sm font-semibold">{scoreComparison.candidate ?? '—'}</p></div>
          <div><span className="text-violet-700/70">Test delta</span><p className="font-mono text-sm font-semibold">{scoreComparison.delta == null ? '—' : scoreComparison.delta >= 0 ? `+${scoreComparison.delta}` : scoreComparison.delta}</p></div>
        </div>}
      </div>}
      {roundDecision && <div className={`rounded-lg border px-3 py-3 ${roundDecision.stop ? 'border-emerald-100 bg-emerald-50 text-emerald-900' : 'border-orange-100 bg-orange-50 text-orange-900'}`}>
        <div className="flex items-center justify-between gap-3"><p className="text-[10px] font-medium uppercase tracking-wide opacity-70">轮次决策</p><span className="rounded bg-white/70 px-1.5 py-0.5 text-[9px] font-medium">{roundDecision.stop ? '停止优化' : '继续下一轮'}</span></div>
        {roundDecision.reason && <p className="mt-2 text-xs leading-5">{roundDecision.reason}</p>}
      </div>}
      {cases?.items && cases.items.length > 0 && <div className="rounded-lg border border-gray-200 bg-white px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-gray-400">诊断 Case</p>
          <span className="text-[9px] text-gray-400">{cases.items.length} 个 Case</span>
        </div>
        <div className="mt-2 divide-y divide-gray-100">
          {cases.items.map((item, index) => {
            const displayId = item.caseId ?? `Case ${index + 1}`
            return <div key={`${item.caseId ?? displayId}-${index}`} className="py-2.5 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium uppercase ${item.type === 'good' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>{item.type ?? 'unknown'}</span>
                  <span className="min-w-0 break-all font-mono text-[10px] text-gray-600">{displayId}</span>
                </div>
                <span className="shrink-0 text-[9px] text-gray-400">诊断样本</span>
              </div>
              {item.summary && <p className="mt-1.5 text-[11px] leading-5 text-gray-600">{item.summary}</p>}
            </div>
          })}
        </div>
      </div>}
      {benchCases && <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-3 text-indigo-900">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-indigo-700/70">Bench 规划</p>
          <span className="text-[10px] text-indigo-700">训练 {benchCases.trainCount ?? 0} · 测试 {benchCases.testCount ?? 0}</span>
        </div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div className="rounded-md bg-white/70 px-2.5 py-2"><p className="text-[9px] text-indigo-600/70">训练 Bench Domain</p>{benchDomains?.trainBenchDomainId && benchCases.items?.find((item) => item.split === 'train')?.template?.ownerUserId ? <a href={`/evolve/bench/domains/${encodeURIComponent(benchCases.items.find((item) => item.split === 'train')!.template!.ownerUserId!)}/${encodeURIComponent(benchDomains.trainBenchDomainId)}`} target="_blank" rel="noopener noreferrer" className="mt-1 block break-all font-mono text-[10px] text-blue-600 hover:text-blue-700">{benchDomains.trainBenchDomainId} ↗</a> : <p className="mt-1 break-all font-mono text-[10px]">{benchDomains?.trainBenchDomainId ?? '—'}</p>}</div>
          <div className="rounded-md bg-white/70 px-2.5 py-2"><p className="text-[9px] text-indigo-600/70">测试 Bench Domain</p>{benchDomains?.testBenchDomainId && benchCases.items?.find((item) => item.split === 'test')?.template?.ownerUserId ? <a href={`/evolve/bench/domains/${encodeURIComponent(benchCases.items.find((item) => item.split === 'test')!.template!.ownerUserId!)}/${encodeURIComponent(benchDomains.testBenchDomainId)}`} target="_blank" rel="noopener noreferrer" className="mt-1 block break-all font-mono text-[10px] text-blue-600 hover:text-blue-700">{benchDomains.testBenchDomainId} ↗</a> : <p className="mt-1 break-all font-mono text-[10px]">{benchDomains?.testBenchDomainId ?? '—'}</p>}</div>
        </div>
        {benchCases.items && benchCases.items.length > 0 && <div className="mt-2 divide-y divide-indigo-100 rounded-md bg-white/70 px-2.5">
          {benchCases.items.map((item, index) => <div key={`${item.taskId ?? item.sourceCaseId}-${index}`} className="flex items-center gap-2 py-2">
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase ${item.split === 'train' ? 'bg-blue-50 text-blue-700' : 'bg-violet-50 text-violet-700'}`}>{item.split ?? '—'}</span>
            {item.template?.ownerUserId && item.template.domainId && item.template.templateName ? <a href={`/evolve/bench/domains/${encodeURIComponent(item.template.ownerUserId)}/${encodeURIComponent(item.template.domainId)}/templates/${encodeURIComponent(item.template.templateName)}`} target="_blank" rel="noopener noreferrer" className="min-w-0 break-all font-mono text-[10px] text-blue-600 hover:text-blue-700">{item.taskId ?? item.sourceCaseId ?? `Task ${index + 1}`} ↗</a> : <span className="min-w-0 break-all font-mono text-[10px] text-gray-600">{item.taskId ?? item.sourceCaseId ?? `Task ${index + 1}`}</span>}
          </div>)}
        </div>}
      </div>}
    </div>
  )
}


function EvolveSidebarLink({ to, label, icon, end = false, activeWhen }: { to: string; label: string; icon: IconName; end?: boolean; activeWhen?: (pathname: string) => boolean }) {
  const location = useLocation()
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
        (activeWhen ? activeWhen(location.pathname) : isActive) ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
      }`}
    >
      <Icon name={icon} className="h-[18px] w-[18px]" />
      {label}
    </NavLink>
  )
}

function EvolveSidebarSubLink({ to, label, activeWhen }: { to: string; label: string; activeWhen: (pathname: string) => boolean }) {
  const location = useLocation()
  const active = activeWhen(location.pathname)
  return (
    <NavLink
      to={to}
      className={`ml-4 flex items-center border-l-2 py-1.5 pl-5 pr-3 text-xs font-medium transition ${active ? 'border-blue-500 bg-blue-50/70 text-blue-700' : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:bg-gray-50 hover:text-gray-800'}`}
    >
      {label}
    </NavLink>
  )
}

function EvolveSidebarEvaluationGroup() {
  const location = useLocation()
  const active = location.pathname.startsWith('/evolve/bench')
  const [expanded, setExpanded] = useState(active)

  useEffect(() => {
    if (active) setExpanded(true)
  }, [active])

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${active ? 'text-blue-700' : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'}`}
      >
        <Icon name="chart" className="h-[18px] w-[18px]" />
        <span>进化评估</span>
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`ml-auto h-5 w-5 shrink-0 transition-transform duration-200 ${active ? 'text-blue-600' : 'text-gray-500'} ${expanded ? 'rotate-90' : ''}`}
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
      </button>
      {expanded && <div className="mt-0.5 space-y-0.5">
        <EvolveSidebarSubLink to="/evolve/bench/domains" label="评估模板" activeWhen={(pathname) => pathname.startsWith('/evolve/bench/domains')} />
        <EvolveSidebarSubLink to="/evolve/bench/runs" label="评估任务" activeWhen={(pathname) => pathname.startsWith('/evolve/bench/runs')} />
      </div>}
    </div>
  )
}

function EvolveSidebarComingSoon({ label }: { label: string }) {
  return (
    <div className="flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2 text-sm text-gray-400" aria-disabled="true">
      <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
      <span>{label}</span>
      <span className="ml-auto rounded bg-gray-100 px-1.5 py-0.5 text-[9px] text-gray-400">建设中</span>
    </div>
  )
}

function EvolveShell({ children }: { children: ReactNode }) {
  const { available, enabled, setEnabled, ownerUserId, setOwnerUserId, ownerUserIds } = useEvolveAdminScope()
  return (
    <div className="flex min-h-[calc(100vh-53px)] bg-[#f5f7fb]">
      <aside className="sticky top-[53px] hidden h-[calc(100vh-53px)] w-56 shrink-0 self-start flex-col overflow-hidden border-r border-gray-200 bg-white px-3 py-5 lg:flex">
        <div className="mb-5 flex items-center gap-2 px-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-white"><Icon name="spark" /></span>
          <div><p className="text-sm font-semibold text-gray-900">Claw进化 <span className="text-[10px] font-normal text-blue-500">(Open 版本)</span></p><p className="text-[10px] text-gray-400">ClawEvolve</p></div>
        </div>
        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">进化中心</div>
          <EvolveSidebarLink to="/evolve" label="进化任务" icon="spark" activeWhen={(pathname) => pathname === '/evolve' || pathname.startsWith('/evolve/tasks') || pathname.startsWith('/evolve/runs') || pathname.startsWith('/evolve/new')} />
          <EvolveSidebarEvaluationGroup />
          <EvolveSidebarLink to="/evolve/packs" label="进化版本" icon="package" />
          <div className="px-3 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wider text-gray-400">专项进化</div>
          <p className="px-3 pb-1 text-[10px] leading-4 text-gray-400">特定模块的独立管理与定向进化</p>
          <EvolveSidebarComingSoon label="Skill 进化" />
          <EvolveSidebarComingSoon label="Memory 进化" />
          <EvolveSidebarComingSoon label="Context 进化" />
          <div className="px-3 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wider text-gray-400">系统工具</div>
          <EvolveSidebarLink to="/evolve/tools/tclog" label="TCLog" icon="file" />
        </nav>
        {available && <div className={`mt-3 shrink-0 rounded-xl border p-3 ${enabled ? 'border-amber-200 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
          <label className="flex cursor-pointer items-center justify-between gap-2 text-xs font-semibold text-gray-700">
            <span>管理员视图</span>
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="h-4 w-4 rounded border-gray-300 text-amber-600" />
          </label>
          {enabled && <>
            <p className="mt-2 text-[10px] leading-4 text-amber-700">可按工号查看数据；Repair 创建页可选择该用户的 Bot，其他操作仍按各页面权限校验。</p>
            <input
              aria-label="按工号筛选"
              list="evolve-admin-owner-options"
              value={ownerUserId}
              onChange={(event) => setOwnerUserId(event.target.value.trim())}
              placeholder="输入工号；留空查看全部"
              className="mt-2 w-full rounded-lg border border-amber-200 bg-white px-2 py-1.5 text-xs text-gray-700 outline-none focus:border-amber-400"
            />
            <datalist id="evolve-admin-owner-options">
              {ownerUserIds.map((owner) => <option key={owner} value={owner} />)}
            </datalist>
          </>}
        </div>}
      </aside>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}


function PackDetail() {
  const navigate = useNavigate()
  const { user } = useClientUser()
  const packId = decodeURIComponent(window.location.pathname.split('/').at(-1) ?? '')
  const [data, setData] = useState<Awaited<ReturnType<typeof api.evolve.getPack>> | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { void api.evolve.getPack(packId).then(setData).catch((e) => setError(e instanceof Error ? e.message : String(e))) }, [packId])
  if (error) return <div className="mx-auto max-w-5xl p-8 text-sm text-red-700">{error}</div>
  if (!data) return <div className="mx-auto max-w-5xl p-8 text-sm text-gray-500">加载中…</div>
  return <div className="mx-auto max-w-5xl px-4 py-7 sm:px-6 lg:px-8">
    <button className="mb-5 text-sm text-gray-500" onClick={() => navigate('/evolve/packs')}>← 返回进化版本</button>
    <h1 className="text-2xl font-semibold">Pack 详情</h1>
    <div className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 text-sm shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-mono text-xs text-gray-500">{data.pack.packId}</p><p className="mt-2">Bot：{data.pack.botId}</p><p className="mt-1">类型：{data.pack.sourceKind}{data.pack.sourceRound ? ` / Round ${data.pack.sourceRound}` : ''}</p><p className="mt-1">生成时间：{formatStepTime(data.pack.createdAt)}</p><p className="mt-1 break-all font-mono text-xs">SHA-256：{data.pack.artifact.sha256}</p></div>{data.pack.userId === user?.userId && <button className={primaryButton} onClick={() => navigate(`/evolve/new?type=pack_restore&packId=${encodeURIComponent(data.pack.packId)}&botEnv=${encodeURIComponent(String(data.sourceTask?.config?.botEnv ?? ''))}`)}>应用 Pack</button>}</div></div>
    <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="font-semibold">生成任务</h2>{data.sourceTask ? <button className="mt-3 text-sm text-blue-600" onClick={() => navigate(`/evolve/runs/${data.sourceTask?.task_id}`)}>{data.sourceTask.task_name || data.sourceTask.task_id}</button> : <p className="mt-3 text-gray-400">来源任务不存在</p>}</section>
    <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="font-semibold">应用任务（{data.applications.length}）</h2><div className="mt-3 divide-y divide-gray-100">{data.applications.map((task) => <button key={task.task_id} className="flex w-full items-center justify-between py-3 text-left text-sm" onClick={() => navigate(`/evolve/runs/${task.task_id}`)}><span>{task.task_name || task.task_id}</span><span className="text-gray-500">{formatStepTime(task.gmt_create)} · {task.status}</span></button>)}{data.applications.length === 0 && <p className="py-6 text-sm text-gray-400">尚未应用</p>}</div></section>
  </div>
}

export default function Evolve() {
  const location = useLocation()
  const { authState } = useClientUser()
  // COSEC: The embedded package owns a separate auth cache from its ClawWeb host;
  // do not issue identity-scoped API requests until this package resolves the user.
  if (authState === 'loading') {
    return <div className="mx-auto max-w-5xl px-4 py-20 text-center text-sm text-gray-500">正在识别当前用户…</div>
  }
  if (authState === 'login_required') {
    return <div className="mx-auto max-w-5xl px-4 py-20 text-center text-sm text-red-600">登录状态无效，请刷新页面后重试。</div>
  }
  let content: ReactNode
  if (location.pathname.startsWith('/evolve/packs/')) content = <PackDetail />
  else if (location.pathname === '/evolve/packs') content = <PackManagement />
  else if (location.pathname === '/evolve/tools/tclog') content = <TCLog />
  else if (location.pathname === '/evolve/tasks') content = <TaskList />
  else if (location.pathname.startsWith('/evolve/repair-runs/')) content = <Repair view="detail" />
  else if (location.pathname.startsWith('/evolve/session-runs/')) content = <SessionAnalysis view="detail" />
  else if (location.pathname.startsWith('/evolve/bench/runs/')) content = <BenchRunDetail basePath="/evolve/bench" />
  else if (location.pathname.includes('/templates/')) content = <BenchTemplateDetail basePath="/evolve/bench" />
  else if (location.pathname.startsWith('/evolve/bench')) content = <BenchDomains basePath="/evolve/bench" />
  else if (location.pathname === '/evolve/new' && new URLSearchParams(location.search).get('type') === 'repair') content = <Repair view="create" />
  else if (location.pathname === '/evolve/new' && new URLSearchParams(location.search).get('type') === 'session_analysis') content = <SessionAnalysis view="create" />
  else if (location.pathname === '/evolve/new') content = <StartEvolution />
  else if (location.pathname.startsWith('/evolve/runs/')) content = <TaskDetail />
  else content = <TaskList />
  return <EvolveAdminScopeProvider><EvolveShell>{content}</EvolveShell></EvolveAdminScopeProvider>
}
