import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  api,
  type RepairAgentInput,
  type RepairAgentMode,
  type RepairBot,
  type RepairCfuseEngine,
  type RepairCreateTaskInput,
  type RepairHistoricalPlan,
  type RepairPlan,
  type RepairStepFailure,
  type RepairTask,
  type RepairTargetEnvironment,
  type RepairToolCall,
} from '../api/client'
import EvolveBotPicker from '../components/EvolveBotPicker'
import EvolveModelFields, {
  DEFAULT_EVOLVE_MODEL,
  EVOLVE_CUSTOM_MODEL,
  EVOLVE_MODEL_OPTIONS,
} from '../components/EvolveModelFields'
import EvolveTaskOverview from '../components/EvolveTaskOverview'
import { insightApi } from '../api/insight'
import { useEvolveAdminScope } from '../features/evolve/admin-scope'

const inputClass = 'w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10'
const primaryButton = 'inline-flex items-center justify-center rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50'
const secondaryButton = 'inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50'
const dangerButton = 'inline-flex items-center justify-center rounded-lg border border-red-200 bg-white px-4 py-2.5 text-sm font-medium text-red-700 shadow-sm transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50'
const REPAIR_OPENCLAW_MODEL_OPTIONS = [
  ...EVOLVE_MODEL_OPTIONS,
  'DeepSeek-V4-Flash-0731',
] as const
type RepairStepView = Pick<NonNullable<RepairTask['currentStep']>, 'stepId' | 'stepNo' | 'phase' | 'status' | 'aisJobId' | 'summary' | 'error' | 'failure'>
type RepairTaskView = RepairTask & {
  steps?: RepairStepView[]
  pendingDecision?: { kind?: string; feedback?: string | null } | null
}
type RepairStepGroup = {
  stepId: string
  stepNo: number
  phase: 'repair_plan' | 'repair_apply'
}
type RepairAisStep = Pick<RepairStepView, 'stepId' | 'stepNo' | 'phase' | 'status'>
type RepairAisContainer = { jobId: string; steps: RepairAisStep[] }
type FeedbackIntent = 'reject_plan' | 'retry_result' | null
type HistoricalPlanLoad =
  | { status: 'loading' }
  | { status: 'loaded'; value: RepairHistoricalPlan }
  | { status: 'error'; message: string }

const TOOL_PURPOSES: Record<string, string> = {
  'repair_control:bootstrap': '载入本次 Repair 的目标、历史记录和可用工具',
  'repair_control:decision_claim': '领取用户决策并继续下一步骤',
  'step_report:running': '记录当前步骤已开始运行',
  'step_report:succeeded': '记录当前步骤已成功完成',
  'step_report:failed': '记录当前步骤执行失败',
  'step_report:waiting_context': '记录当前步骤因等待上下文而暂停',
  'antlogs:search': '查询相关日志，补充故障证据',
  'baas_read:fs_list': '查看 Bot 容器中的目录内容',
  'baas_read:fs_find': '在 Bot 容器中查找文件',
  'baas_read:fs_stat': '查看 Bot 容器中的文件或目录信息',
  'baas_read:fs_read': '读取 Bot 容器中的文件片段',
  'baas_read:fs_search': '在 Bot 容器文件中搜索文本',
  'baas_read:process_list': '查看 Bot 容器中的运行进程',
  'baas_read:port_list': '查看 Bot 容器中的监听端口',
  'baas_read:http_get': '检查 Bot 容器内的本地服务是否可访问',
  'ocb_read:current_target': '确认当前 Bot 的运行目标',
  'ocb_read:engine_config_read': '读取当前 Bot 的引擎配置',
  'ocb_read:identity_file_read': '读取 Bot 的身份与行为配置文件',
  'ocb_write:restart_bot': '执行已批准的 Bot 重启',
  'ocb_write:engine_config_patch': '执行已批准的引擎配置变更',
  'ocb_write:engine_config_replace': '查看历史引擎配置整表更新',
  'ocb_write:identity_file_replace': '执行已批准的 Bot 身份与行为配置更新',
  'cfuse_login:authorize': '等待用户完成 CodeFuse 登录授权',
}

const TOOL_FALLBACK_PURPOSES: Record<string, string> = {
  repair_control: '处理 Repair 流程控制',
  step_report: '更新 Repair 步骤状态',
  antlogs: '查询故障日志',
  baas_read: '检查 Bot 容器运行状态',
  baas_write: '执行已批准的容器修复动作',
  ocb_read: '读取 Bot 控制面信息',
  ocb_write: '执行已批准的 Bot 控制面操作',
  cfuse_login: '处理 CodeFuse 登录授权',
}

function actionAnchor(actionId: string, scope = 'current'): string {
  return scope === 'current'
    ? `repair-action-${encodeURIComponent(actionId)}`
    : `repair-${encodeURIComponent(scope)}-action-${encodeURIComponent(actionId)}`
}

function toolCallAnchor(toolCallId: string): string {
  return `repair-tool-call-${encodeURIComponent(toolCallId)}`
}

function toolPurpose(call: RepairToolCall, actionSummary: string | null): string {
  if (call.purpose) return call.purpose
  if (actionSummary) return `执行已批准动作：${actionSummary}`
  if (call.actionId) return '执行已批准的修复动作'
  if (call.toolName === 'baas_write' && call.operation.startsWith('apply_action:')) {
    return '执行已批准的容器修复动作'
  }
  return TOOL_PURPOSES[`${call.toolName}:${call.operation}`]
    ?? TOOL_FALLBACK_PURPOSES[call.toolName]
    ?? '执行一项受控工具调用'
}
function formatTime(value: number | string | null | undefined): string {
  if (value == null || value === '') return '—'
  const date = new Date(typeof value === 'number' && value < 10_000_000_000 ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

function statusView(status: string, phase?: 'repair_plan' | 'repair_apply'): { label: string; className: string } {
  if (status === 'running' && phase === 'repair_plan') {
    return { label: '正在分析并生成方案', className: 'bg-blue-50 text-blue-700' }
  }
  if (status === 'running' && phase === 'repair_apply') {
    return { label: '正在执行修复', className: 'bg-blue-50 text-blue-700' }
  }
  if (status === 'succeeded' && phase === 'repair_plan') {
    return { label: '方案已生成', className: 'bg-emerald-50 text-emerald-700' }
  }
  if (status === 'succeeded' && phase === 'repair_apply') {
    return { label: '执行已完成', className: 'bg-emerald-50 text-emerald-700' }
  }
  const values: Record<string, { label: string; className: string }> = {
    created: { label: '准备中', className: 'bg-gray-100 text-gray-700' },
    pending: { label: '准备中', className: 'bg-gray-100 text-gray-700' },
    dispatching: { label: '正在启动容器', className: 'bg-blue-50 text-blue-700' },
    executing: { label: '处理中', className: 'bg-blue-50 text-blue-700' },
    dispatched: { label: '已调度', className: 'bg-blue-50 text-blue-700' },
    running: { label: '进行中', className: 'bg-blue-50 text-blue-700' },
    waiting_approval: { label: '等待批准方案', className: 'bg-amber-50 text-amber-800' },
    waiting_acceptance: { label: '等待确认结果', className: 'bg-violet-50 text-violet-700' },
    waiting_context: { label: '等待补充上下文', className: 'bg-orange-50 text-orange-700' },
    completed: { label: '已完成', className: 'bg-emerald-50 text-emerald-700' },
    succeeded: { label: '已成功', className: 'bg-emerald-50 text-emerald-700' },
    failed: { label: '失败', className: 'bg-red-50 text-red-700' },
    interrupted: { label: '已中断', className: 'bg-orange-50 text-orange-700' },
    canceled: { label: '已取消', className: 'bg-gray-100 text-gray-600' },
  }
  return values[status] ?? { label: `未知状态（${status}）`, className: 'bg-gray-100 text-gray-700' }
}

function executionStateLabel(state: string): string {
  return ({
    starting: '启动中',
    dispatching: '启动中',
    running: '运行中',
    waiting_decision: '等待用户决策',
    ended: '已结束',
  } as Record<string, string>)[state] ?? `未知状态（${state}）`
}

function StatusPill({ status, phase }: { status: string; phase?: 'repair_plan' | 'repair_apply' }) {
  const view = statusView(status, phase)
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${view.className}`}>{view.label}</span>
}

function DetailRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-4 py-1.5 text-xs"><dt className="text-gray-500">{label}</dt><dd className={`min-w-0 break-all text-right font-medium text-gray-900 ${mono ? 'font-mono' : ''}`}>{value}</dd></div>
}

function AisContainersDrawer({
  containers,
  selectedJobId,
  selectedFromStep,
  currentJobId,
  execution,
  onClose,
}: {
  containers: RepairAisContainer[]
  selectedJobId: string
  selectedFromStep: boolean
  currentJobId?: string | null
  execution?: RepairTask['execution']
  onClose: () => void
}) {
  const drawerRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = [...(drawerRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [])]
        .filter((element) => element.getAttribute('aria-hidden') !== 'true')
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!drawerRef.current?.contains(document.activeElement)) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose])

  const orderedContainers = [...containers].sort((left, right) => {
    if (left.jobId === selectedJobId) return -1
    if (right.jobId === selectedJobId) return 1
    return Math.max(...right.steps.map((step) => step.stepNo)) - Math.max(...left.steps.map((step) => step.stepNo))
  })

  return <div className="fixed inset-0 z-50">
    <button type="button" aria-label="关闭 AIS 容器信息" className="absolute inset-0 h-full w-full bg-slate-950/30 backdrop-blur-[1px]" onClick={onClose} />
    <aside ref={drawerRef} role="dialog" aria-modal="true" aria-labelledby="repair-ais-containers-title" className="absolute right-0 top-0 flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
      <header className="flex items-start justify-between gap-4 border-b border-gray-200 px-6 py-5">
        <div>
          <h2 id="repair-ais-containers-title" className="text-lg font-semibold text-gray-950">AIS 容器</h2>
          <p className="mt-1 text-xs leading-5 text-gray-500">本任务共关联 {containers.length} 个 AIS Job；同一个容器可以连续承载多个 Repair 步骤。</p>
        </div>
        <button type="button" autoFocus aria-label="关闭" className="rounded-lg p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700" onClick={onClose}>✕</button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
        {orderedContainers.map((container) => {
          const isCurrent = container.jobId === currentJobId
          const isSelected = container.jobId === selectedJobId
          return <article key={container.jobId} data-ais-job={container.jobId} aria-current={isSelected ? 'true' : undefined} aria-label={`AIS Job ${container.jobId}${isSelected ? '，当前查看' : ''}`} className={`rounded-xl border p-4 ${isSelected ? 'border-blue-300 bg-blue-50/30 ring-2 ring-blue-100' : 'border-gray-200 bg-white'}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[11px] font-medium text-gray-500">AIS Job</p>
                <p className="mt-1 break-all font-mono text-sm font-semibold text-gray-900">{container.jobId}</p>
              </div>
              <div className="flex flex-wrap justify-end gap-2">{isSelected && <span className="rounded-full bg-indigo-100 px-2.5 py-1 text-[11px] font-medium text-indigo-700">{selectedFromStep ? '本步骤关联' : '当前查看'}</span>}{isCurrent ? <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[11px] font-medium text-blue-700">当前容器</span> : <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] font-medium text-gray-600">历史容器</span>}</div>
            </div>
            <div className="mt-4">
              <p className="text-xs font-medium text-gray-600">关联步骤</p>
              <div className="mt-2 space-y-2">{container.steps.map((step) => <div key={step.stepId} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-gray-50 px-3 py-2">
                <div><p className="text-xs font-medium text-gray-800">{step.phase === 'repair_plan' ? '方案步骤' : '执行步骤'} · 第 {step.stepNo} 步</p><p className="mt-0.5 break-all font-mono text-[10px] text-gray-400">{step.stepId}</p></div>
                <StatusPill status={step.status} phase={step.phase} />
              </div>)}</div>
            </div>
            {isCurrent && execution && <dl className="mt-4 border-t border-gray-100 pt-3">
              <DetailRow label="容器状态" value={executionStateLabel(execution.state)} />
              {execution.decisionWindowExpired && <DetailRow label="用户确认窗口" value="已结束" />}
              {!execution.decisionWindowExpired && execution.decisionDeadlineAt != null && <DetailRow label="等待用户确认至" value={formatTime(execution.decisionDeadlineAt)} />}
            </dl>}
            <a aria-label={`在 AIS Studio 查看 AIS Job ${container.jobId}`} className="mt-4 inline-flex text-xs font-medium text-blue-600 hover:text-blue-700" href={`https://aistudio.alipay.com/project/job/detail/${encodeURIComponent(container.jobId)}`} target="_blank" rel="noreferrer">在 AIS Studio 查看 ↗</a>
          </article>
        })}
      </div>
    </aside>
  </div>
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

type ApplyVerdict = 'verified' | 'partially_verified' | 'failed' | 'blocked' | 'unknown'
type ApplyActionStatus = 'succeeded' | 'failed' | 'skipped' | 'blocked' | 'unknown'
type ApplyVerificationStatus = 'verified' | 'partially_verified' | 'failed' | 'blocked' | 'unknown'
type ApplyResultView = {
  verdict: ApplyVerdict
  summary: string
  actions: Array<{
    actionId: string
    status: ApplyActionStatus
    attemptCount: number
    verification: { status: ApplyVerificationStatus; evidence: string[] }
  }>
}

const APPLY_VERDICT_VIEW: Record<ApplyVerdict, { label: string; className: string }> = {
  verified: { label: '已完成验证', className: 'bg-emerald-50 text-emerald-700' },
  partially_verified: { label: '等待最终验证', className: 'bg-amber-50 text-amber-700' },
  failed: { label: '修复失败', className: 'bg-red-50 text-red-700' },
  blocked: { label: '验证未完成', className: 'bg-orange-50 text-orange-700' },
  unknown: { label: '结果待确认', className: 'bg-gray-100 text-gray-700' },
}

const APPLY_ACTION_STATUS_LABELS: Record<ApplyActionStatus, string> = {
  succeeded: '执行成功',
  failed: '执行失败',
  skipped: '未执行',
  blocked: '执行受阻',
  unknown: '状态待确认',
}

const APPLY_VERIFICATION_STATUS_LABELS: Record<ApplyVerificationStatus, string> = {
  verified: '验证通过',
  partially_verified: '部分验证',
  failed: '验证失败',
  blocked: '未完成验证',
  unknown: '尚未验证',
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function hasOwnKey<T extends string>(value: unknown, values: Record<T, unknown>): value is T {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(values, value)
}

function applyResultView(value: unknown): ApplyResultView | null {
  const result = recordValue(value)
  if (!result) return null
  const verdict = hasOwnKey(result.verdict, APPLY_VERDICT_VIEW)
    ? result.verdict
    : 'unknown'
  const summary = typeof result.summary === 'string' && result.summary.trim()
    ? result.summary.trim()
    : ''
  const rawActions = Array.isArray(result.actions) ? result.actions : []
  const actions = rawActions.flatMap((rawAction) => {
    const action = recordValue(rawAction)
    if (!action || typeof action.actionId !== 'string' || !action.actionId.trim()) return []
    const status = hasOwnKey(action.status, APPLY_ACTION_STATUS_LABELS)
      ? action.status
      : 'unknown'
    const verification = recordValue(action.verification)
    const verificationStatus = hasOwnKey(verification?.status, APPLY_VERIFICATION_STATUS_LABELS)
      ? verification.status
      : 'unknown'
    const evidence = Array.isArray(verification?.evidence)
      ? verification.evidence.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      : []
    return [{
      actionId: action.actionId,
      status,
      attemptCount: Array.isArray(action.attempts) ? action.attempts.length : 0,
      verification: { status: verificationStatus, evidence },
    }]
  })
  if (!summary && actions.length === 0 && !('verdict' in result)) return null
  return { verdict, summary, actions }
}

function ApplyResultPanel({
  value,
  plan,
  running,
}: {
  value: unknown
  plan: RepairPlan | null | undefined
  running: boolean
}) {
  const result = applyResultView(value)
  const technicalRecord = recordValue(value)
  const hasTechnicalDetails = technicalRecord != null && Object.keys(technicalRecord).length > 0
  if (!result) {
    return <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">执行与验证结果</h2>
      <p className="mt-2 text-sm leading-6 text-gray-500">{running
        ? 'Agent 正在执行获批操作并收集验证证据，完成后会在这里展示。'
        : '执行结果产物暂未加载成功，请稍后刷新。'}</p>
      {hasTechnicalDetails && <details className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
        <summary className="cursor-pointer text-xs font-medium text-gray-600">查看技术详情</summary>
        <pre className="mt-3 max-h-[560px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-4 text-xs leading-5 text-gray-100">{safeJson(value)}</pre>
      </details>}
    </section>
  }

  const verdictView = APPLY_VERDICT_VIEW[result.verdict]
  const nextStep = result.verdict === 'partially_verified' || result.verdict === 'blocked'
    ? '原始问题尚未完整验证，请按批准方案中的验证方式确认是否恢复。'
    : null
  return <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-base font-semibold text-gray-900">执行与验证结果</h2>
      <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${verdictView.className}`}>{verdictView.label}</span>
    </div>
    <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-700">{result.summary || '系统已记录执行结果，详细状态如下。'}</p>
    {result.actions.length > 0 && <div className="mt-4 space-y-3">
      {result.actions.map((action) => {
        const planAction = plan?.actions.find((item) => item.actionId === action.actionId)
        return <article key={action.actionId} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-medium text-gray-900">{planAction?.summary || action.actionId}</h3>
              {planAction?.summary && <p className="mt-1 font-mono text-[10px] text-gray-400">{action.actionId}</p>}
            </div>
            <span className="rounded bg-white px-2 py-1 text-xs font-medium text-gray-700">{APPLY_ACTION_STATUS_LABELS[action.status]}</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-600">
            <span>执行尝试：{action.attemptCount} 次</span>
            <span>验证状态：{APPLY_VERIFICATION_STATUS_LABELS[action.verification.status]}</span>
          </div>
          {action.verification.evidence.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-4 text-xs leading-5 text-gray-600">
            {action.verification.evidence.map((item, index) => <li key={`${action.actionId}-verification-${index}`}>{item}</li>)}
          </ul>}
        </article>
      })}
    </div>}
    {nextStep && <div className="mt-4 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800"><strong>下一步：</strong>{nextStep}</div>}
    <details className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <summary className="cursor-pointer text-xs font-medium text-gray-600">查看完整技术结果</summary>
      <pre className="mt-3 max-h-[560px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-4 text-xs leading-5 text-gray-100">{safeJson(value)}</pre>
    </details>
  </section>
}

function botEnvironment(bot: RepairBot | undefined): RepairTargetEnvironment | null {
  const value = bot?.env?.trim().toLowerCase()
  if (value === 'pre' || value === 'prepub') return 'pre'
  if (value === 'prod' || value === 'gray') return 'prod'
  return null
}
function localTimeToUnix(value: string): number | null {
  if (!value) return null
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : null
}

function chosenModel(choice: string, customModel: string): string {
  return choice === EVOLVE_CUSTOM_MODEL ? customModel.trim() : choice
}

function RepairOpenClawModelFields({
  choice,
  customValue,
  onChoiceChange,
  onCustomValueChange,
}: {
  choice: string
  customValue: string
  onChoiceChange: (value: string) => void
  onCustomValueChange: (value: string) => void
}) {
  return <>
    <label className="text-xs font-medium text-gray-600">
      模型
      <select
        aria-label="OpenClaw 模型"
        className={`${inputClass} mt-1.5`}
        value={choice}
        onChange={(event) => onChoiceChange(event.target.value)}
      >
        {REPAIR_OPENCLAW_MODEL_OPTIONS.map((model) => <option key={model} value={model}>{model}</option>)}
        <option value={EVOLVE_CUSTOM_MODEL}>自定义模型名称</option>
      </select>
    </label>
    {choice === EVOLVE_CUSTOM_MODEL && <label className="text-xs font-medium text-gray-600">
      自定义模型名称
      <input
        aria-label="OpenClaw 自定义模型名称"
        className={`${inputClass} mt-1.5`}
        maxLength={128}
        value={customValue}
        onChange={(event) => onCustomValueChange(event.target.value)}
        placeholder="请输入 OpenAI-compatible 模型名"
      />
    </label>}
  </>
}

function executionCanContinue(task: RepairTask): boolean {
  if (task.execution?.state !== 'waiting_decision') return false
  const rawExpiry = task.execution.leaseExpiresAt
  const rawDeadline = task.execution.decisionDeadlineAt
  if (rawExpiry == null || rawExpiry === '' || rawDeadline == null || rawDeadline === '') return false
  const expiry = typeof rawExpiry === 'number' ? rawExpiry : Number(rawExpiry)
  const deadline = typeof rawDeadline === 'number' ? rawDeadline : Number(rawDeadline)
  const now = Math.floor(Date.now() / 1000)
  return Number.isFinite(expiry) && Number.isFinite(deadline) && expiry > now && deadline > now
}

function agentInputForTask(task: RepairTask, modelApiKey = ''): RepairAgentInput {
  if (task.agentMode === 'cfuse') {
    if (task.cfuseEngine !== 'cfuse' && task.cfuseEngine !== 'claude-code') {
      throw new Error('此历史 Repair 使用已停用的 Codex Engine，不能继续执行')
    }
    return {
      agentMode: 'cfuse',
      ...(task.cfuseEngine ? { cfuseEngine: task.cfuseEngine } : {}),
      ...(task.cfuseModel ? { cfuseModel: task.cfuseModel } : {}),
    }
  }
  return {
    agentMode: 'openclaw',
    llmUseDefault: task.llmUseDefault,
    ...(!task.llmUseDefault && task.llmModel ? { llmModel: task.llmModel } : {}),
    ...(modelApiKey.trim() ? { llmApiKey: modelApiKey.trim() } : {}),
  }
}

function CreateRepair({
  modelApiKey,
  setModelApiKey,
}: {
  modelApiKey: string
  setModelApiKey: (value: string) => void
}) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const {
    enabled: adminMode,
    ownerUserId: adminOwnerUserId,
    setOwnerUserId: setAdminOwnerUserId,
    ownerUserIds: adminOwnerUserIds,
  } = useEvolveAdminScope()
  const rawInsightImprovementId = searchParams.get('improvementId')?.trim() ?? ''
  const parsedInsightImprovementId = Number(rawInsightImprovementId)
  const insightImprovementId = /^\d+$/.test(rawInsightImprovementId)
    && Number.isSafeInteger(parsedInsightImprovementId)
    && parsedInsightImprovementId > 0
    ? parsedInsightImprovementId
    : null
  const adminConsentToken = searchParams.get('adminConsent')?.trim() ?? ''
  const insightRequestId = useMemo(
    () => `insight-repair-${insightImprovementId ?? 'manual'}-${Date.now()}`,
    [insightImprovementId],
  )
  const [bots, setBots] = useState<RepairBot[]>([])
  const [botsLoading, setBotsLoading] = useState(true)
  const [loadedOwnerId, setLoadedOwnerId] = useState('')
  const [botId, setBotId] = useState('')
  const [botSelectionKey, setBotSelectionKey] = useState('')
  const [symptom, setSymptom] = useState('')
  const [repairDirection, setRepairDirection] = useState('')
  const [insightEvidenceCount, setInsightEvidenceCount] = useState<number | null>(null)
  const [insightSessionIds, setInsightSessionIds] = useState<string[]>([])
  const [traceId, setTraceId] = useState('')
  const [relatedTaskId, setRelatedTaskId] = useState('')
  const [errorText, setErrorText] = useState('')
  const [fromTime, setFromTime] = useState('')
  const [toTime, setToTime] = useState('')
  const [agentMode, setAgentMode] = useState<RepairAgentMode>('openclaw')
  const [deepDiagnostics, setDeepDiagnostics] = useState(false)
  const [llmUseDefault, setLlmUseDefault] = useState(true)
  const [llmModelChoice, setLlmModelChoice] = useState<string>(DEFAULT_EVOLVE_MODEL)
  const [customLlmModel, setCustomLlmModel] = useState('')
  const [cfuseEngine, setCfuseEngine] = useState<RepairCfuseEngine>('cfuse')
  const [cfuseModelChoice, setCfuseModelChoice] = useState<string>(DEFAULT_EVOLVE_MODEL)
  const [customCfuseModel, setCustomCfuseModel] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (adminMode) {
      setBots([])
      setBotsLoading(false)
      setLoadedOwnerId('')
      setBotId('')
      setBotSelectionKey('')
      return undefined
    }
    let active = true
    api.repair.bots()
      .then(({ userId, bots: items }) => {
        if (!active) return
        setBots(items)
        setLoadedOwnerId(userId)
        setBotId('')
        setBotSelectionKey('')
      })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Bot 列表加载失败') })
      .finally(() => { if (active) setBotsLoading(false) })
    return () => { active = false }
  }, [adminMode])

  const loadAdminBots = async () => {
    const ownerId = adminOwnerUserId.trim()
    setError('')
    if (!ownerId) {
      setError('请输入目标 Bot 的 Owner 工号')
      return
    }
    setBotsLoading(true)
    try {
      const result = await api.repair.bots(ownerId)
      setBots(result.bots)
      // The endpoint authenticates the requested owner scope; its userId field
      // remains the current actor for backwards compatibility.
      setLoadedOwnerId(ownerId)
      setBotId('')
      setBotSelectionKey('')
    } catch (cause) {
      setBots([])
      setLoadedOwnerId('')
      setBotId('')
      setBotSelectionKey('')
      setError(cause instanceof Error ? cause.message : '目标用户的 Bot 列表加载失败')
    } finally {
      setBotsLoading(false)
    }
  }

  useEffect(() => {
    if (!adminMode || !loadedOwnerId || adminOwnerUserId.trim() === loadedOwnerId) return
    setBots([])
    setLoadedOwnerId('')
    setBotId('')
    setBotSelectionKey('')
  }, [adminMode, adminOwnerUserId, loadedOwnerId])

  useEffect(() => {
    if (insightImprovementId == null) return undefined
    let active = true
    insightApi.improvement(insightImprovementId)
      .then((detail) => {
        if (!active) return
        setSymptom(detail.title)
        setRepairDirection(detail.suggestedAction ?? detail.userGuidance ?? '')
        setInsightEvidenceCount(detail.evidenceCount)
        setInsightSessionIds([...new Set(detail.evidence.map((item) => item.sessionId))])
        setBotId(detail.botId)
        setBotSelectionKey(detail.botId)
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : '改进项加载失败')
      })
    return () => { active = false }
  }, [insightImprovementId])

  const selectedBot = bots.find((bot) => bot.botId === botId)
  const targetEnvironment = botEnvironment(selectedBot)

  const create = async () => {
    setError('')
    if (adminMode && !loadedOwnerId) return setError('请先按 Owner 工号查询 Bot')
    if (!botId) return setError('请选择要修复的 Bot')
    if (!targetEnvironment) return setError('所选 Bot 的运行环境缺失或暂不支持')
    if (!symptom.trim()) return setError('请描述需要修复的问题')
    if (Boolean(fromTime) !== Boolean(toTime)) return setError('自定义时间范围需要同时填写开始和结束时间')
    const from = localTimeToUnix(fromTime)
    const to = localTimeToUnix(toTime)
    if (fromTime && (from == null || to == null || to <= from)) return setError('自定义时间范围不合法')
    const llmModel = chosenModel(llmModelChoice, customLlmModel)
    const cfuseModel = chosenModel(cfuseModelChoice, customCfuseModel)
    if (agentMode === 'openclaw' && !llmUseDefault && !llmModel) return setError('请输入自定义 LLM 模型名称')
    if (agentMode === 'cfuse' && !cfuseModel) return setError('请输入自定义 cfuse 模型名称')

    setBusy(true)
    try {
      const selectedAgent: RepairAgentInput = agentMode === 'openclaw'
        ? {
            agentMode: 'openclaw',
            llmUseDefault,
            ...(!llmUseDefault ? {
              llmModel,
              ...(modelApiKey.trim() ? { llmApiKey: modelApiKey.trim() } : {}),
            } : {}),
          }
        : { agentMode: 'cfuse', cfuseEngine, cfuseModel }
      const input: RepairCreateTaskInput = {
        ...(adminMode ? { targetUserId: loadedOwnerId } : {}),
        targetEnvironment,
        botId,
        ...(insightImprovementId != null ? {
          insightImprovementId,
          insightRequestId,
          repairDirection: repairDirection.trim() || undefined,
          ...(adminConsentToken ? { persistAutoRepairGrant: true, adminConsentToken } : {}),
        } : {}),
        symptom: symptom.trim(),
        diagnosticMode: deepDiagnostics ? 'deep' : 'observe',
        ...selectedAgent,
        ...(traceId.trim() ? { traceId: traceId.trim() } : {}),
        ...(relatedTaskId.trim() ? { relatedTaskId: relatedTaskId.trim() } : {}),
        ...(errorText.trim() ? { errorText: errorText.trim() } : {}),
        ...(from != null && to != null ? { timeRange: { from, to } } : {}),
      }
      const task = await api.repair.create(input)
      setModelApiKey('')
      navigate(`/evolve/repair-runs/${encodeURIComponent(task.taskId)}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Repair Task 创建失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve')} className="mb-5 text-sm text-gray-500 hover:text-gray-800">‹ 返回任务列表</button>
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <header className="border-b border-gray-100 px-6 py-5">
          <p className="flex items-center gap-2 text-sm font-medium text-blue-600">Bot 修复<span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-blue-700">Beta</span></p>
          <h1 className="mt-2 text-xl font-semibold text-gray-950">发起 Bot 修复</h1>
          <p className="mt-1 text-sm leading-6 text-gray-500">Repair Agent 会进行深度诊断、生成修复方案，并支持通过多轮交互补充要求；只有方案获批后才会执行修复。</p>
          {insightImprovementId != null && <div className="mt-4 rounded-xl border border-indigo-200 bg-indigo-50/70 px-4 py-3 text-xs leading-5 text-indigo-900"><p className="font-semibold">效果中心改进项 #{insightImprovementId}</p><p className="mt-1">系统已带入 {insightEvidenceCount ?? '—'} 条 Evidence、{insightSessionIds.length} 个 Session 和已有修复方向；Repair Agent 会优先通过接口读取完整证据。</p>{adminConsentToken && <p className="mt-1 font-medium text-emerald-700">Owner 持续授权确认已附带，本次创建后将进入持续授权范围。</p>}</div>}
        </header>
        <div className="p-6">
          <div className="grid gap-7 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          <div className="space-y-6">
          <section>
            <h2 className="text-sm font-semibold text-gray-900">修复对象</h2>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {adminMode && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 sm:col-span-2">
                <label className="text-xs font-medium text-amber-900">目标 Bot Owner 工号</label>
                <div className="mt-1.5 flex gap-2">
                  <input
                    aria-label="目标 Bot Owner 工号"
                    list="repair-admin-owner-options"
                    maxLength={128}
                    className={inputClass}
                    value={adminOwnerUserId}
                    onChange={(event) => {
                      setAdminOwnerUserId(event.target.value.trim())
                      setBots([])
                      setLoadedOwnerId('')
                      setBotId('')
                      setBotSelectionKey('')
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        void loadAdminBots()
                      }
                    }}
                    placeholder="输入工号后查询该用户的 Bot"
                  />
                  <button type="button" disabled={botsLoading} className={secondaryButton} onClick={() => void loadAdminBots()}>{botsLoading ? '查询中…' : '查询 Bot'}</button>
                </div>
                <datalist id="repair-admin-owner-options">
                  {adminOwnerUserIds.map((ownerId) => <option key={ownerId} value={ownerId} />)}
                </datalist>
                <p className="mt-2 text-[11px] leading-5 text-amber-700">查询后只展示该 Owner 名下当前支持 Repair 的个人 Bot；任务会冻结所选 Owner、Bot 和运行环境。</p>
              </div>}
              <div><span className="text-xs font-medium text-gray-600">Bot <span className="text-red-500">*</span></span><div className="mt-1.5"><EvolveBotPicker bots={bots} value={botSelectionKey} disabled={botsLoading} emptyText={botsLoading ? '正在加载…' : '当前没有可用 Bot'} onChange={(key, bot) => { setBotSelectionKey(key); setBotId(bot.botId) }} /></div></div>
              <label className="text-xs font-medium text-gray-600">运行环境<input aria-label="运行环境" className={`${inputClass} mt-1.5 bg-gray-50`} value={targetEnvironment ?? '环境未知或暂不支持'} readOnly /></label>
            </div>
          </section>

          <section className="border-t border-gray-100 pt-6">
            <h2 className="text-sm font-semibold text-gray-900">Repair Agent</h2>
            <div role="radiogroup" aria-label="执行器" className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className={`cursor-pointer rounded-xl border p-4 ${agentMode === 'openclaw' ? 'border-blue-500 bg-blue-50' : 'border-gray-200'}`}>
                <span className="flex items-start gap-3">
                  <input type="radio" name="repair-agent-mode" value="openclaw" checked={agentMode === 'openclaw'} onChange={() => setAgentMode('openclaw')} className="mt-0.5 h-4 w-4 border-gray-300 text-blue-600" />
                  <span><span className="block text-sm font-medium text-gray-900">OpenClaw</span><span className="mt-1 block text-xs leading-5 text-gray-500">在 AIS 容器内安装 OpenClaw，使用默认或指定的 LLM 配置。</span></span>
                </span>
              </label>
              <label className="cursor-not-allowed rounded-xl border border-gray-200 bg-gray-50 p-4 text-gray-400">
                <span className="flex items-start gap-3">
                  <input type="radio" name="repair-agent-mode" value="cfuse" checked={agentMode === 'cfuse'} disabled className="mt-0.5 h-4 w-4 cursor-not-allowed border-gray-300 text-blue-600" />
                  <span><span className="flex items-center gap-2 text-sm font-medium text-gray-500">cfuse<span className="rounded-full bg-gray-200 px-2 py-0.5 text-[10px] font-medium text-gray-600">待开放</span></span><span className="mt-1 block text-xs leading-5 text-gray-400">cfuse 执行器正在接入中。</span></span>
                </span>
              </label>
            </div>

            {agentMode === 'openclaw' ? (
              <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
                <label className="flex cursor-pointer items-start gap-2">
                  <input aria-label="使用默认 LLM 配置" type="checkbox" checked={llmUseDefault} onChange={(event) => setLlmUseDefault(event.target.checked)} className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600" />
                  <span><span className="block text-xs font-medium text-gray-700">使用默认 LLM 配置</span><span className="mt-0.5 block text-[11px] text-gray-500">使用 Snapshot 中已有的 OpenClaw 模型配置；默认模式不覆盖模型或 Token。</span></span>
                </label>
                {!llmUseDefault && <div className="mt-4 grid gap-4 border-t border-gray-200 pt-4 sm:grid-cols-2">
                  <RepairOpenClawModelFields choice={llmModelChoice} customValue={customLlmModel} onChoiceChange={setLlmModelChoice} onCustomValueChange={setCustomLlmModel} />
                  <label className="text-xs font-medium text-gray-600 sm:col-span-2">Token（可选）<input aria-label="模型 Token" type="password" autoComplete="new-password" className={`${inputClass} mt-1.5`} value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} placeholder="留空则继续使用 AIS 默认 Token" /><span className="mt-1 block text-[11px] font-normal text-amber-600">Token 只保留在当前页面内存，并仅发送给本次 AIS execution。</span></label>
                </div>}
              </div>
            ) : (
              <div className="mt-4 grid gap-4 rounded-xl border border-gray-200 bg-gray-50 p-4 sm:grid-cols-2">
                <label className="text-xs font-medium text-gray-600">Engine<select aria-label="cfuse Engine" className={`${inputClass} mt-1.5`} value={cfuseEngine} onChange={(event) => setCfuseEngine(event.target.value as RepairCfuseEngine)}><option value="cfuse">cfuse</option><option value="claude-code">Claude Code（CC）</option></select></label>
                <EvolveModelFields choice={cfuseModelChoice} customValue={customCfuseModel} onChoiceChange={setCfuseModelChoice} onCustomValueChange={setCustomCfuseModel} selectAriaLabel="cfuse 模型" customAriaLabel="cfuse 自定义模型名称" customClassName="text-xs font-medium text-gray-600 sm:col-span-2" inputClassName={inputClass} customPlaceholder="请输入模型名称" />
              </div>
            )}
          </section>

          <section className="border-t border-gray-100 pt-6">
            <h2 className="text-sm font-semibold text-gray-900">诊断权限</h2>
            <label className={`mt-3 flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${deepDiagnostics ? 'border-amber-400 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
              <input aria-label="允许深度诊断 Shell" type="checkbox" checked={deepDiagnostics} onChange={(event) => setDeepDiagnostics(event.target.checked)} className="mt-0.5 h-4 w-4 rounded border-gray-300 text-amber-600" />
              <span><span className="block text-sm font-medium text-gray-900">允许目标 Bot 深度诊断 Shell</span><span className="mt-1 block text-xs leading-5 text-gray-600">开启后，Repair Agent 可在本任务固定的 Bot 和环境中执行任意诊断命令，包括读取、联网和临时实验；这等价于授予该容器当前用户的命令执行权限。不开启时仍可广泛读取运行态，但不能提交原始 Shell。</span></span>
            </label>
          </section>

          <section className="border-t border-gray-100 pt-6">
            <h2 className="text-sm font-semibold text-gray-900">问题描述</h2>
            <label className="mt-3 block text-xs font-medium text-gray-600">现象 <span className="text-red-500">*</span><textarea aria-label="问题现象" className={`${inputClass} mt-1.5 min-h-32 resize-y`} maxLength={4000} value={symptom} onChange={(event) => setSymptom(event.target.value)} placeholder="描述用户看到的现象、期望行为和已经尝试过的操作" /></label>
            {insightImprovementId != null && <label className="mt-4 block text-xs font-medium text-gray-600">修复方向 / Spec（可选）<textarea aria-label="修复方向" className={`${inputClass} mt-1.5 min-h-24 resize-y`} maxLength={5000} value={repairDirection} onChange={(event) => setRepairDirection(event.target.value)} placeholder="说明希望检查或修改的配置、Skill、权限或运行环境，以及不能修改的范围。" /><span className="mt-1 block text-[11px] font-normal text-gray-500">这段内容会作为 Repair Agent 的执行约束；Session 和完整 Evidence 由系统自动带入。</span></label>}
            <details className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
              <summary className="cursor-pointer text-sm font-medium text-gray-700">补充可选线索</summary>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <label className="text-xs font-medium text-gray-600">Trace ID<input aria-label="Trace ID" className={`${inputClass} mt-1.5`} maxLength={256} value={traceId} onChange={(event) => setTraceId(event.target.value)} /></label>
                <label className="text-xs font-medium text-gray-600">相关任务 ID<input aria-label="相关任务 ID" className={`${inputClass} mt-1.5`} maxLength={256} value={relatedTaskId} onChange={(event) => setRelatedTaskId(event.target.value)} /></label>
                <label className="text-xs font-medium text-gray-600 sm:col-span-2">错误文本<textarea aria-label="错误文本" className={`${inputClass} mt-1.5 min-h-24 resize-y`} maxLength={2000} value={errorText} onChange={(event) => setErrorText(event.target.value)} /></label>
                <label className="text-xs font-medium text-gray-600">开始时间<input aria-label="开始时间" type="datetime-local" className={`${inputClass} mt-1.5`} value={fromTime} onChange={(event) => setFromTime(event.target.value)} /></label>
                <label className="text-xs font-medium text-gray-600">结束时间<input aria-label="结束时间" type="datetime-local" className={`${inputClass} mt-1.5`} value={toTime} onChange={(event) => setToTime(event.target.value)} /></label>
              </div>
              <p className="mt-3 text-[11px] text-gray-500">不填写时使用服务端默认时间窗；自定义范围最长 6 小时。</p>
            </details>
          </section>
          {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          </div>
          <EvolveTaskOverview
            label="修复"
            subtitle="先生成方案，批准后再执行"
            stages={[
              ['生成修复方案', '收集证据并规划最小修复范围'],
              ['人工审批', '确认方案后才允许执行写操作'],
              ['执行与验证', '应用修复并验证结果'],
            ]}
            deliverables={[
              ['证据', '问题上下文与定位线索'],
              ['修复方案', '步骤、范围与风险'],
              ['执行结果', '实际变更与运行日志'],
              ['验证结论', '效果与后续建议'],
            ]}
          />
          </div>
        </div>
        <footer className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4"><button className={secondaryButton} onClick={() => navigate('/evolve')}>取消</button><button disabled={busy} className={primaryButton} onClick={() => void create()}>{busy ? '正在创建…' : '生成修复方案'}</button></footer>
      </div>
    </div>
  )
}

const PLAN_QUALITY_VIEW: Record<NonNullable<RepairPlan['quality']>, { label: string; className: string }> = {
  verified: { label: '证据充分', className: 'bg-emerald-50 text-emerald-700' },
  partially_verified: { label: '部分证据', className: 'bg-amber-50 text-amber-700' },
  blocked: { label: '调查受阻', className: 'bg-orange-50 text-orange-700' },
  unknown: { label: '尚未确认', className: 'bg-gray-100 text-gray-700' },
}

const REPAIR_FAILURE_STAGE_LABELS: Record<string, string> = {
  model_output_parse: '模型输出解析',
  model_output_security: '模型输出安全检查',
  model_output_correction: '输出纠正约束',
  user_facing_language_validation: '中文内容校验',
  plan_validation: '修复方案结构校验',
  apply_validation: '执行结果结构校验',
  agent_invoke: 'Agent 调用',
  response_parse: '模型响应解析',
  agent_closeout: '超时收口',
  artifact_upload: '结果产物上传',
  preflight: '执行前检查',
  cfuse_preflight: 'CodeFuse 执行前检查',
  agents_add: 'Agent 工作区初始化',
  tool_policy: '工具权限配置',
}

const REPAIR_FAILURE_REASON_LABELS: Record<string, string> = {
  invalid_json_or_root: '输出不是合法的单个 JSON 对象',
  format_retry_invalid: '格式纠正后仍不是合法的单个 JSON 对象',
  credential_detected: '模型输出中检测到凭据，已阻止原始结果保存',
  locked_field_changed: '输出纠正修改了锁定字段',
  system_context_changed: '启动期间检测到 Component 版本或受控上下文发生变化，已阻止混合版本执行',
  language_retry_invalid: '中文输出纠正流程未得到合规结果',
  language_retry_semantic_mismatch: '中文纠正改变了受保护的技术字面量或结构',
  user_facing_chinese_required: '用户可见内容缺少中文说明',
  invalid_plan_shape: '修复方案结构不符合契约',
  invalid_apply_shape: '执行结果结构不符合契约',
  authentication_failed: '模型认证失败',
  rate_limited: '模型服务限流',
  timeout: 'Agent 调用超时',
  timeout_exhausted: '超时收口重试次数已用尽',
  result_invalid: 'Agent 超时后原地收口结果无效',
  closeout_failed: '超时收口执行失败',
  session_mismatch: '收口会话与原会话不一致',
  http_rejected: '对象存储拒绝了结果产物上传',
  transport_failed: '连接对象存储时发生网络错误',
  refresh_failed: '刷新结果产物上传地址失败',
  refresh_invalid: '刷新后的结果产物上传地址不匹配当前任务',
}

const REPAIR_FAILURE_LANGUAGE_RULE_LABELS: Record<NonNullable<RepairStepFailure['rule']>, string> = {
  han_required: '用户可见内容必须包含中文',
  chinese_dominance: '中文内容应占主体',
}

const REPAIR_FAILURE_RETRY_BRANCH_LABELS: Record<NonNullable<RepairStepFailure['retryBranch']>, string> = {
  not_allowed: '当前流程不允许执行中文纠正',
  already_consumed: '此前的纠正机会已经使用，本次未执行中文纠正',
  session_missing: '缺少可继续的 Agent 会话，未执行中文纠正',
  session_mismatch: '中文纠正返回了不同的 Agent 会话',
  output_invalid: '中文纠正结果不是合法的单个 JSON 对象',
  semantic_mismatch: '中文纠正改变了受保护的技术字面量或结构',
  contract_invalid: '中文纠正结果不符合结构化输出契约',
  still_non_chinese: '中文纠正后该字段仍不符合中文要求',
}

function RepairFailureDetails({
  failure,
  prominent = false,
}: {
  failure?: RepairStepFailure | null
  prominent?: boolean
}) {
  if (!failure?.stage && !failure?.reason) return null
  return <div className={`mt-3 space-y-1.5 border-t border-current/10 pt-3 leading-6 ${prominent ? 'text-sm' : 'text-[11px]'}`}>
    {failure.stage && <p>失败阶段：{REPAIR_FAILURE_STAGE_LABELS[failure.stage] ?? `组件执行（${failure.stage}）`}</p>}
    {failure.reason && <p>诊断原因：{REPAIR_FAILURE_REASON_LABELS[failure.reason] ?? `组件报告（${failure.reason}）`}</p>}
    {failure.field && <p>失败字段：<span className="font-mono">{failure.field}</span></p>}
    {failure.rule && <p>校验规则：{REPAIR_FAILURE_LANGUAGE_RULE_LABELS[failure.rule]}</p>}
    {failure.retryBranch && <p>纠正状态：{REPAIR_FAILURE_RETRY_BRANCH_LABELS[failure.retryBranch]}</p>}
    {failure.httpStatus != null && <p>HTTP 状态：<span className="font-mono">{failure.httpStatus}</span></p>}
    {failure.providerCode && <p>OSS 错误码：<span className="font-mono">{failure.providerCode}</span></p>}
    {failure.providerRequestId && <p>OSS Request ID：<span className="font-mono">{failure.providerRequestId}</span></p>}
    {failure.retryCount != null && <p>上传重试：已尝试 {failure.retryCount} 次</p>}
  </div>
}

function CurrentStepFailureAlert({
  step,
  showRetry,
  canRetry,
  retrying,
  onRetry,
}: {
  step: RepairTask['currentStep']
  showRetry: boolean
  canRetry: boolean
  retrying: boolean
  onRetry: () => void
}) {
  if (!step || step.status !== 'failed' || (!step.error && !step.failure)) return null
  const versionChanged = step.failure?.reason === 'system_context_changed'
  const retryMessage = showRetry && !canRetry
    ? '该方案步骤可以重新运行，但只有具备任务操作权限的用户可以操作。'
    : canRetry
    ? '可以重新运行方案阶段。系统会创建一个不继承本次失败上下文的新方案步骤。'
    : step.failure?.retryable === true
      ? '该错误可以重试，但当前任务状态不支持直接启动新的方案步骤。'
    : step.failure?.retryable === false
      ? '该错误不能由 Agent 自动重试，请先排除原因后重新运行。'
      : '服务端未声明该错误是否可以重试，请先排除原因后重新运行。'
  const actionMessage = versionChanged
    ? '如果失败发生在 Component 更新期间，请使用独立版本目录并原子切换；否则请核查受控上下文是否被其他进程改写。'
    : '请根据上方诊断原因处理后，再重新发起或恢复 Repair。'
  return <section role="alert" aria-labelledby="repair-current-failure-heading" className="mt-5 rounded-2xl border border-red-300 bg-red-50 p-5 shadow-sm">
    <div className="flex items-start gap-3">
      <span aria-hidden="true" className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-600 text-lg font-semibold text-white">!</span>
      <div className="min-w-0">
        <h2 id="repair-current-failure-heading" className="text-base font-semibold text-red-950">Repair 步骤执行失败</h2>
        <p className="mt-1 text-sm leading-6 text-red-900">{repairErrorMessage(step.error) || 'Repair Agent 未返回可用结果。'}</p>
        <RepairFailureDetails failure={step.failure} prominent />
        <div className="mt-4 rounded-xl border border-red-200 bg-white/70 p-3 text-sm leading-6 text-red-900">
          <p className="font-medium">接下来怎么做</p>
          <p className="mt-1">{retryMessage}</p>
          <p>{actionMessage}</p>
          {showRetry && <button type="button" disabled={!canRetry || retrying} title={!canRetry ? '当前用户无任务操作权限' : undefined} className="mt-3 inline-flex items-center justify-center rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-50" onClick={onRetry}>{retrying ? '正在重新运行…' : '重新运行方案步骤'}</button>}
        </div>
      </div>
    </div>
  </section>
}

function CurrentStepRecoveryAlert({ step }: { step: RepairTask['currentStep'] }) {
  const raw = step?.output?.recovery
  if (!step || step.status !== 'running' || !raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const recovery = raw as Record<string, unknown>
  if (recovery.schemaVersion !== 'ce-repair-recovery-progress/v1'
    || recovery.kind !== 'result_finalization') return null
  const attempt = typeof recovery.attempt === 'number' ? recovery.attempt : null
  const maximum = typeof recovery.maxAttempts === 'number' ? recovery.maxAttempts : null
  const systemFallback = recovery.mode === 'system_fallback'
  return <section role="status" aria-live="polite" className="mt-5 rounded-2xl border border-amber-300 bg-amber-50 p-5 shadow-sm">
    <div className="flex items-start gap-3">
      <span aria-hidden="true" className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-500 text-sm font-semibold text-white">↻</span>
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-amber-950">正在安全整理执行结果</h2>
        <p className="mt-1 text-sm leading-6 text-amber-900">{systemFallback
          ? 'Agent 多次整理仍未通过校验，系统正在根据中央审计生成最小可信结果。'
          : `结果校验未通过，正在自动纠正${attempt != null && maximum != null ? `（${attempt}/${maximum}）` : ''}。`}</p>
        <p className="mt-2 text-xs leading-5 text-amber-800">此过程只整理结果，不会重新执行配置修改、Cron 或其他修复操作。</p>
      </div>
    </div>
  </section>
}

function PlanPanel({
  plan,
  stepSummary,
  stepStatus,
  stepError,
  stepFailure,
  defaultExpanded,
  title = '修复方案',
  description = '方案不可变；批准时会绑定当前 artifact digest。',
  idPrefix = 'current',
  failurePresented = false,
}: {
  plan: RepairPlan | null | undefined
  stepSummary?: string | null
  stepStatus?: string
  stepError?: string | null
  stepFailure?: RepairStepFailure | null
  defaultExpanded: boolean
  title?: string
  description?: string
  idPrefix?: string
  failurePresented?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const sectionIdPrefix = `repair-plan-${encodeURIComponent(idPrefix)}`

  if (!plan) {
    if (stepStatus === 'failed') {
      if (failurePresented) {
        return <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="text-base font-semibold text-gray-900">{title}</h2><p className="mt-2 text-sm leading-6 text-gray-500">本步骤未生成方案；失败原因和重试入口已在页面上方单独展示。</p></section>
      }
      return <section className="rounded-2xl border border-red-200 bg-white p-5 shadow-sm"><h2 className="text-base font-semibold text-gray-900">{title}</h2><p className="mt-2 text-sm leading-6 text-gray-600">方案生成失败。</p><div role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-xs leading-5 text-red-700"><p>失败原因：{repairErrorMessage(stepError) || 'Repair Agent 未返回可解析的结构化方案。'}</p><RepairFailureDetails failure={stepFailure} /></div></section>
    }
    const message = ['created', 'pending', 'dispatching', 'dispatched', 'running', 'executing'].includes(stepStatus ?? '')
      ? 'Agent 正在收集只读证据并生成方案，完成后会在这里展示。'
      : stepStatus === 'succeeded'
        ? '方案产物暂未加载成功，请稍后刷新。'
        : '本步骤未生成可展示的方案，请查看工作流步骤中的原因。'
    return <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="text-base font-semibold text-gray-900">{title}</h2><p className="mt-2 text-sm leading-6 text-gray-500">{message}</p></section>
  }

  const groups = [
    ['已确认事实', plan.diagnosis.facts],
    ['推断', plan.diagnosis.inferences],
    ['仍待确认', plan.diagnosis.unknowns],
  ] as const
  const qualityView = plan.quality ? PLAN_QUALITY_VIEW[plan.quality] : null
  const recommendation = plan.recommendation
  const emptyActionMessage = recommendation?.disposition === 'no_change'
    ? '方案明确建议不执行任何写操作。该结论只适用于上方说明的证据范围，不代表目标 Bot 已被完整证明健康。'
    : recommendation?.disposition === 'insufficient_evidence'
      ? '现有证据不足，尚未形成可安全批准的修复操作。请先补充线索并重新规划。'
      : '这是旧版空方案，未声明“无需变更”还是“证据不足”，不能据此确认无需修复。'
  return (
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary className="cursor-pointer list-none px-5 py-4">
          <div className="flex items-center justify-between gap-4">
            <div><h2 className="text-base font-semibold text-gray-900">{title}</h2><p className="mt-1 text-xs text-gray-500">{description}</p></div>
            <span className="shrink-0 text-xs font-medium text-blue-600">{expanded ? '收起方案' : '展开方案'}</span>
          </div>
        </summary>
        <div className="space-y-5 border-t border-gray-100 p-5">
          <section aria-labelledby={`${sectionIdPrefix}-recommendation-heading`} className="rounded-xl border border-blue-100 bg-blue-50/40 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 id={`${sectionIdPrefix}-recommendation-heading`} className="text-sm font-semibold text-gray-900">方案结论</h3>
              {qualityView && <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${qualityView.className}`}>{qualityView.label}</span>}
            </div>
            {recommendation
              ? <><p className="mt-2 whitespace-pre-wrap text-sm font-medium leading-6 text-gray-900">{recommendation.summary}</p><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-gray-600">{recommendation.reason}</p>{recommendation.nextSteps && recommendation.nextSteps.length > 0 && <div className="mt-3"><p className="text-xs font-medium text-gray-600">建议后续</p><ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-5 text-gray-600">{recommendation.nextSteps.map((item, index) => <li key={`next-step-${index}`}>{item}</li>)}</ul></div>}</>
              : <><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">{stepSummary || '旧版方案未提供可展示的结论摘要。'}</p><p role="status" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">这是旧版方案，未包含结构化方案结论和证据质量；页面不会根据自然语言摘要猜测其含义。</p></>}
          </section>
          <section aria-labelledby={`${sectionIdPrefix}-diagnosis-heading`} className="space-y-3">
            <h3 id={`${sectionIdPrefix}-diagnosis-heading`} className="text-sm font-semibold text-gray-900">诊断依据</h3>
            <div data-plan-diagnosis-grid className="grid grid-cols-1 gap-3">{groups.map(([title, items]) => <div key={title} className="min-w-0 rounded-xl border border-gray-200 bg-gray-50 p-4"><h3 className="text-xs font-semibold text-gray-700">{title}</h3>{items.length ? <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-5 text-gray-600">{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul> : <p className="mt-2 text-xs text-gray-400">无</p>}</div>)}</div>
          </section>
          <section aria-labelledby={`${sectionIdPrefix}-actions-heading`} className="space-y-3">
            <h3 id={`${sectionIdPrefix}-actions-heading`} className="text-sm font-semibold text-gray-900">拟执行操作（{plan.actions.length}）</h3>
            {plan.actions.length === 0
              ? <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-700">{emptyActionMessage}</div>
              : <div className="space-y-3">{plan.actions.map((action, index) => <article id={actionAnchor(action.actionId, idPrefix)} key={action.actionId} className="scroll-mt-6 rounded-xl border border-gray-200 p-4"><div className="flex flex-wrap items-center gap-2"><span className="flex h-6 w-6 items-center justify-center rounded bg-blue-600 text-xs font-semibold text-white">{index + 1}</span><h3 className="font-medium text-gray-900">{action.summary}</h3><span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-600">{action.actionId}</span><span className="rounded bg-sky-50 px-2 py-0.5 text-[10px] text-sky-700">{action.type === 'container_command' ? '容器命令' : 'OCB 操作'}</span></div><div className="mt-3 grid gap-3 text-xs md:grid-cols-2"><div><p className="font-medium text-gray-500">风险</p><p className="mt-1 whitespace-pre-wrap leading-5 text-gray-700">{action.risk}</p></div><div><p className="font-medium text-gray-500">验证</p><p className="mt-1 whitespace-pre-wrap leading-5 text-gray-700">{action.verification}</p></div></div>{action.command && <div className="mt-3"><p className="text-xs font-medium text-gray-500">准确命令</p><pre className="mt-1 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-3 text-xs text-gray-100">{action.command}</pre></div>}{action.operation && <details className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3"><summary className="cursor-pointer text-xs font-medium text-gray-600">准确操作</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-3 text-xs text-gray-100">{safeJson(action.operation)}</pre></details>}{action.rollback && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800"><strong>回滚：</strong>{action.rollback}</p>}</article>)}</div>}
          </section>
        </div>
      </details>
    </section>
  )
}

function repairErrorMessage(value?: string | null): string | null {
  if (!value) return null
  if (value === 'Repair Agent output is not one JSON object') {
    return 'Repair Agent 输出不是单个合法 JSON 对象'
  }
  return value
}

const BUSINESS_TOOL_NAMES = new Set([
  'antlogs',
  'baas_read',
  'baas_write',
  'arca_read',
  'arca_write',
  'ocb_read',
  'ocb_write',
])

function toolErrorText(call: RepairToolCall): string | null {
  if (!call.error) return null
  return typeof call.error === 'string'
    ? call.error
    : `${call.error.code ? `${call.error.code}: ` : ''}${call.error.message || '调用失败'}`
}

function ToolTechnicalDetails({ call }: { call: RepairToolCall }) {
  return <details className="mt-3 border-t border-gray-100 pt-3"><summary className="cursor-pointer text-xs font-medium text-gray-600">技术信息</summary><dl className="mt-3 space-y-1"><DetailRow label="状态" value={statusView(call.status, call.phase).label} /><DetailRow label="工具" value={call.toolName} mono /><DetailRow label="Operation" value={call.operation} mono />{call.actionId && <DetailRow label="Action ID" value={call.actionId} mono />}<DetailRow label="Tool Call ID" value={call.toolCallId} mono /><DetailRow label="创建时间" value={formatTime(call.createdAt)} /><DetailRow label="更新时间" value={formatTime(call.updatedAt)} /></dl></details>
}

function ControlToolCallCard({ call }: { call: RepairToolCall }) {
  return <article data-audit-layout="control" id={toolCallAnchor(call.toolCallId)} className="scroll-mt-6 rounded-lg border border-gray-200 bg-white px-3 py-2.5"><div className="flex flex-wrap items-center gap-x-3 gap-y-1"><div className="flex min-w-0 flex-1 items-center gap-2"><p className="truncate text-xs font-medium text-gray-800">{toolPurpose(call, null)}</p><StatusPill status={call.status} /></div><p className="min-w-0 truncate text-[11px] text-gray-500">{call.resultSummary || '中央控制记录已更新。'}</p><span className="font-mono text-[10px] text-gray-400">{formatTime(call.updatedAt)}</span></div></article>
}

function BusinessToolCallCard({
  call,
  action,
}: {
  call: RepairToolCall
  action?: NonNullable<RepairPlan['actions']>[number]
}) {
  const error = toolErrorText(call)
  const anchor = toolCallAnchor(call.toolCallId)
  const titleId = `${anchor}-title`
  const resultSummary = call.resultSummary || ({
    succeeded: '调用已完成。',
    failed: '调用失败，展开查看原因。',
    unknown: '调用结果未知，展开查看详情。',
    canceled: '调用已取消。',
  }[call.status] ?? '调用结果尚未完成。')
  return (
    <article
      aria-labelledby={titleId}
      data-audit-layout="business"
      id={anchor}
      className="scroll-mt-6 rounded-xl border border-gray-200 bg-white p-4"
    >
      <header>
        <div className="min-w-0">
          <h3 id={titleId} className="text-sm font-medium leading-6 text-gray-900">{toolPurpose(call, action?.summary ?? null)}</h3>
          <p className="mt-1 text-[11px] leading-5 text-gray-500"><span className="font-medium">结果：</span>{resultSummary}</p>
          {call.conclusion?.nextAction && <p className="text-[11px] leading-5 text-blue-800"><span className="font-medium">后续动作：</span>{call.conclusion.nextAction}</p>}
        </div>
      </header>
      <details data-audit-details className="group mt-3 border-t border-gray-100 pt-3">
        <summary className="cursor-pointer text-right text-xs font-medium text-blue-600">
          <span className="group-open:hidden">展开详情</span>
          <span className="hidden group-open:inline">收起详情</span>
        </summary>
        <div className="mt-3 space-y-2 text-xs leading-5">
          <p className="text-gray-700"><span className="font-semibold text-gray-500">执行对象：</span>{call.executionTarget || '本次 Repair 授权范围内的目标'}</p>
          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700">{error}</p>}
          <p className="text-gray-900"><span className="font-semibold text-gray-500">结论：</span>{call.conclusion?.text || '结论尚未记录。'}</p>
        </div>
        {call.safeInvocation && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-blue-600">{call.safeInvocation.kind === 'readonly_command' ? '展开服务端生成的只读命令' : call.safeInvocation.kind === 'diagnostic_command' ? '展开 Agent 生成的诊断命令' : '展开服务端校验的只读操作'}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-3 text-xs leading-5 text-gray-100">{call.safeInvocation.kind === 'typed_operation' ? safeJson({ operation: call.safeInvocation.operation, params: call.safeInvocation.params }) : call.safeInvocation.command}</pre></details>}
        {action && <a className="mt-3 inline-flex text-xs font-medium text-blue-600 hover:text-blue-700" href={`#${actionAnchor(action.actionId)}`}>查看批准方案中的准确命令或操作 ↑</a>}
        {call.conclusion?.evidenceToolCallIds && call.conclusion.evidenceToolCallIds.length > 0 && <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-gray-500"><span>证据调用：</span>{call.conclusion.evidenceToolCallIds.map((toolCallId) => <a key={toolCallId} className="font-mono text-blue-600 hover:text-blue-700" href={`#${toolCallAnchor(toolCallId)}`}>{toolCallId}</a>)}</div>}
        <ToolTechnicalDetails call={call} />
      </details>
    </article>
  )
}

function ToolCallsPanel({
  calls,
  steps,
  currentStepId,
  currentPhase,
  approvedPlanStepId,
  plan,
  truncated,
}: {
  calls: RepairToolCall[]
  steps: RepairStepGroup[]
  currentStepId?: string
  currentPhase?: 'repair_plan' | 'repair_apply'
  approvedPlanStepId?: string
  plan?: RepairPlan | null
  truncated?: boolean
}) {
  const assignedCallIds = new Set<string>()
  const groups = steps.flatMap((step) => {
    const stepCalls = calls.filter((call) => call.stepId === step.stepId)
    if (stepCalls.length === 0) return []
    stepCalls.forEach((call) => assignedCallIds.add(call.toolCallId))
    return [{ ...step, calls: stepCalls }]
  })
  const unassigned = calls.filter((call) => !assignedCallIds.has(call.toolCallId))
  if (unassigned.length > 0) {
    groups.push({ stepId: 'unassigned', stepNo: 0, phase: 'repair_plan', calls: unassigned })
  }
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-gray-900">工具调用审计</h2><p className="mt-1 text-xs text-gray-500">ClawWeb 中央授权并执行的 cfuse 登录、日志、运行时和 OCB 操作。</p></div><span className="text-xs text-gray-400">{calls.length} 次</span></div>
      {truncated && <p role="status" className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">审计记录较多，当前仅展示前 500 条；结论仍按已展示调用准确关联。</p>}
      <div className="mt-4 space-y-3">{calls.length === 0 ? <p className="rounded-xl border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">暂无工具调用</p> : groups.map((group) => {
        const open = group.stepId === currentStepId || group.calls.some((call) => ['pending', 'executing', 'failed', 'unknown'].includes(call.status))
        return <details data-step-group={group.stepId} open={open} key={group.stepId} className="rounded-xl border border-gray-200 bg-gray-50/60 p-3"><summary className="cursor-pointer list-none"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold text-gray-800">{group.stepId === 'unassigned' ? '未关联步骤' : `${group.phase === 'repair_plan' ? '方案步骤' : '执行步骤'} · 第 ${group.stepNo} 步`}</p>{group.stepId !== 'unassigned' && <p className="mt-1 font-mono text-[10px] text-gray-400">{group.stepId}</p>}</div><p className="text-xs text-gray-500">{group.calls.length} 次调用 · {open ? '默认展开' : '点击展开'}</p></div></summary><div className="mt-3 space-y-2 border-t border-gray-200 pt-3">{group.calls.map((call) => {
        const mayUseCurrentPlan = currentPhase === 'repair_apply'
          && call.stepId === currentStepId
          && Boolean(approvedPlanStepId)
          && plan?.stepId === approvedPlanStepId
        const action = mayUseCurrentPlan && call.actionId
          ? plan?.actions.find((item) => item.actionId === call.actionId)
          : undefined
        return BUSINESS_TOOL_NAMES.has(call.toolName)
          ? <BusinessToolCallCard key={call.toolCallId} call={call} action={action} />
          : <ControlToolCallCard key={call.toolCallId} call={call} />
      })}</div></details>
      })}</div>
    </section>
  )
}

function RepairDetail({
  modelApiKey,
  setModelApiKey,
}: {
  modelApiKey: string
  setModelApiKey: (value: string) => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const taskId = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1) ?? '')
  const [task, setTask] = useState<RepairTask | null>(null)
  const [error, setError] = useState('')
  const [decisionError, setDecisionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const [feedback, setFeedback] = useState('')
  const [feedbackIntent, setFeedbackIntent] = useState<FeedbackIntent>(null)
  const [feedbackContext, setFeedbackContext] = useState('')
  const [relayNotice, setRelayNotice] = useState('')
  const [cfuseAuthCode, setCfuseAuthCode] = useState('')
  const [cfuseLoginError, setCfuseLoginError] = useState('')
  const [cfuseLoginBusy, setCfuseLoginBusy] = useState(false)
  const [sharingBusy, setSharingBusy] = useState(false)
  const [selectedAisJobId, setSelectedAisJobId] = useState<string | null>(null)
  const [selectedAisSource, setSelectedAisSource] = useState<'task' | 'step'>('task')
  const [expandedHistoricalPlans, setExpandedHistoricalPlans] = useState<Record<string, boolean>>({})
  const [historicalPlans, setHistoricalPlans] = useState<Record<string, HistoricalPlanLoad>>({})
  const aisDrawerTriggerRef = useRef<HTMLElement | null>(null)
  const taskAisButtonRef = useRef<HTMLButtonElement | null>(null)
  const taskHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const closeAisContainers = useCallback(() => {
    const trigger = aisDrawerTriggerRef.current
    setSelectedAisJobId(null)
    window.requestAnimationFrame(() => {
      const stableFallback = taskAisButtonRef.current && !taskAisButtonRef.current.disabled
        ? taskAisButtonRef.current
        : taskHeadingRef.current
      const focusTarget = trigger?.isConnected ? trigger : stableFallback
      focusTarget?.focus()
    })
  }, [])
  const fulfilling = useRef(new Set<string>())
  const lastFulfillAttempt = useRef(new Map<string, number>())

  const load = useCallback(async () => {
    if (!taskId) return
    try {
      setTask(await api.repair.get(taskId))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Repair Task 加载失败')
    }
  }, [taskId])

  const toggleHistoricalPlan = async (stepId: string) => {
    const isExpanded = expandedHistoricalPlans[stepId] === true
    setExpandedHistoricalPlans((current) => ({ ...current, [stepId]: !isExpanded }))
    if (isExpanded || ['loading', 'loaded'].includes(historicalPlans[stepId]?.status ?? '')) return
    setHistoricalPlans((current) => ({ ...current, [stepId]: { status: 'loading' } }))
    try {
      const value = await api.repair.getStepPlan(taskId, stepId)
      setHistoricalPlans((current) => ({ ...current, [stepId]: { status: 'loaded', value } }))
    } catch {
      setHistoricalPlans((current) => ({
        ...current,
        [stepId]: {
          status: 'error',
          message: '无法读取经校验的历史方案，请稍后重试。',
        },
      }))
    }
  }

  useEffect(() => {
    let active = true
    queueMicrotask(() => { if (active) void load() })
    return () => { active = false }
  }, [load])
  const taskStatus = task?.status
  const currentStepId = task?.currentStep?.stepId
  const pendingDecisionKind = (task as RepairTaskView | null)?.pendingDecision?.kind ?? null
  const decisionContext = `${taskId}:${currentStepId ?? ''}:${taskStatus ?? ''}:${pendingDecisionKind ?? ''}`
  const activeFeedbackIntent = feedbackContext === decisionContext ? feedbackIntent : null
  useEffect(() => {
    if (!taskStatus || ['completed', 'failed', 'canceled'].includes(taskStatus)) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [taskStatus, load])

  const ownerCanOperate = task?.canOperate === true
  const adminCanOperate = task?.canAdminOperate === true
  const taskCanOperate = (ownerCanOperate || adminCanOperate) && task.executionSupported === true
  const pendingContextCalls = useMemo(
    () => (task?.toolCalls ?? []).filter((call) => (
      call.status === 'pending' && call.requiresBrowserRelay === true
    )),
    [task?.toolCalls],
  )
  const pendingToolCalls = useMemo(
    () => ownerCanOperate
      ? (task?.toolCalls ?? []).filter((call) => call.status === 'pending' && call.requiresBrowserRelay === true)
      : [],
    [task?.toolCalls, ownerCanOperate],
  )
  useEffect(() => {
    if (taskStatus !== 'running' || pendingToolCalls.length === 0) return
    for (const call of pendingToolCalls) {
      const now = Date.now()
      if (fulfilling.current.has(call.toolCallId) || now - (lastFulfillAttempt.current.get(call.toolCallId) ?? 0) < 5000) continue
      fulfilling.current.add(call.toolCallId)
      lastFulfillAttempt.current.set(call.toolCallId, now)
      void api.repair.fulfillToolCall(taskId, call.toolCallId)
        .then(() => load())
        .catch(() => setRelayNotice('上下文调用正在等待当前登录态或由另一个页面处理，页面会继续重试。'))
        .finally(() => fulfilling.current.delete(call.toolCallId))
    }
  }, [taskStatus, taskId, pendingToolCalls, load])

  const cfuseLoginCall = useMemo(() => {
    const currentStepId = task?.currentStep?.stepId
    return [...(task?.toolCalls ?? [])].reverse().find((call) => (
      call.toolName === 'cfuse_login' && (!currentStepId || call.stepId === currentStepId)
    )) ?? null
  }, [task?.currentStep?.stepId, task?.toolCalls])
  const cfuseLoginUrl = typeof cfuseLoginCall?.cfuseLoginUrl === 'string'
    ? cfuseLoginCall.cfuseLoginUrl
    : ''

  const submitCfuseAuthCode = async () => {
    if (!task || !ownerCanOperate || !cfuseLoginCall) return
    const authCode = cfuseAuthCode.trim()
    if (!authCode) return setCfuseLoginError('请输入 CodeFuse 授权页面返回的 AuthCode')
    setCfuseLoginBusy(true)
    setCfuseLoginError('')
    try {
      await api.repair.submitCfuseAuthCode(task.taskId, cfuseLoginCall.toolCallId, authCode)
      setCfuseAuthCode('')
      await load()
    } catch (cause) {
      setCfuseLoginError(cause instanceof Error ? cause.message : 'AuthCode 提交失败')
    } finally {
      setCfuseLoginBusy(false)
    }
  }

  const newExecutionAgentInput = (startsNewExecution: boolean): RepairAgentInput | null => {
    if (!task) return null
    if (!task.executionSupported) {
      setDecisionError(task.executionBlock?.message || '当前 Repair 的执行器已不再受支持')
      return null
    }
    if (startsNewExecution && task.agentMode === 'openclaw' && task.openclawUsesCustomApiKey && !modelApiKey.trim()) {
      setDecisionError('该任务使用了自定义 Token，请重新输入后再拉起新的 AIS Job')
      return null
    }
    return agentInputForTask(task, startsNewExecution ? modelApiKey : '')
  }

  const decidePlan = async (decision: 'approve' | 'reject') => {
    if (!task?.currentStep || !taskCanOperate) return
    setDecisionError('')
    const digest = typeof task.currentStep.output?.artifactDigest === 'string' ? task.currentStep.output.artifactDigest : ''
    if (decision === 'approve' && !digest) return setDecisionError('当前方案缺少可批准的 artifact digest')
    if (decision === 'reject' && !feedback.trim()) return setDecisionError('驳回方案时请填写原因')
    const completesWithoutExecution = decision === 'approve'
      && task.plan?.recommendation?.disposition === 'no_change'
    const agentInput = newExecutionAgentInput(!completesWithoutExecution && !executionCanContinue(task))
    if (!agentInput) return
    setBusyAction(decision)
    try {
      const updated = decision === 'approve'
        ? await api.repair.decidePlan(task.taskId, { decision, artifactDigest: digest, ...agentInput })
        : await api.repair.decidePlan(task.taskId, { decision, reason: feedback.trim(), ...agentInput })
      setTask(updated); setFeedback(''); setFeedbackIntent(null); setFeedbackContext(''); setModelApiKey('')
    } catch (cause) { setDecisionError(cause instanceof Error ? cause.message : '方案决策失败') }
    finally { setBusyAction('') }
  }

  const decideResult = async (decision: 'accept' | 'retry') => {
    if (!task || !(task.canOperate === true || task.canAdminOperate === true) || (decision === 'retry' && !task.executionSupported)) return
    setDecisionError('')
    if (decision === 'retry' && !feedback.trim()) return setDecisionError('继续修复时请说明仍未解决的问题')
    const agentInput = decision === 'retry' ? newExecutionAgentInput(!executionCanContinue(task)) : null
    if (decision === 'retry' && !agentInput) return
    setBusyAction(decision)
    try {
      const updated = decision === 'accept'
        ? await api.repair.decideResult(task.taskId, { decision })
        : await api.repair.decideResult(task.taskId, { decision, reason: feedback.trim(), ...agentInput! })
      setTask(updated); setFeedback(''); setFeedbackIntent(null); setFeedbackContext(''); setModelApiKey('')
    } catch (cause) { setDecisionError(cause instanceof Error ? cause.message : '结果决策失败') }
    finally { setBusyAction('') }
  }

  const resume = async () => {
    if (!task || !taskCanOperate) return
    setDecisionError('')
    const reusesFailedPlanExecution = task.status === 'failed'
      && task.currentStep?.phase === 'repair_plan'
      && task.currentStep.status === 'failed'
      && executionCanContinue(task)
    const agentInput = newExecutionAgentInput(!reusesFailedPlanExecution)
    if (!agentInput) return
    setBusyAction('resume')
    try { setTask(await api.repair.resume(task.taskId, agentInput)); setModelApiKey('') }
    catch (cause) { setDecisionError(cause instanceof Error ? cause.message : '恢复 Repair 失败') }
    finally { setBusyAction('') }
  }

  const toggleSharing = async () => {
    if (!task || !task.canManageShare || sharingBusy) return
    setSharingBusy(true)
    setDecisionError('')
    try {
      setTask(await api.repair.setTaskShared(task.taskId, !task.shared))
    } catch (cause) {
      setDecisionError(cause instanceof Error ? cause.message : '更新分享设置失败')
    } finally {
      setSharingBusy(false)
    }
  }

  const terminate = async () => {
    if (!task || !task.canTerminate || busyAction) return
    const confirmed = window.confirm(
      '确定终止本次 Repair 实验吗？\n\n系统会停止当前 AIS 执行并取消后续流程，但不会撤销已经完成的修复操作。',
    )
    if (!confirmed) return
    setBusyAction('terminate')
    setDecisionError('')
    try {
      const updated = await api.repair.terminate(task.taskId, '用户终止本次 Repair 实验')
      setTask(updated)
      if (updated.termination?.status === 'remote_stop_failed') {
        setDecisionError('Repair 已终止，但 AIS 停止请求失败；控制面已阻止该执行继续回报。')
      }
    } catch (cause) {
      setDecisionError(cause instanceof Error ? cause.message : '终止 Repair 失败')
    } finally {
      setBusyAction('')
    }
  }

  if (!task) return <div className="mx-auto max-w-5xl px-4 py-20 text-center"><p className={`text-sm ${error ? 'text-red-600' : 'text-gray-500'}`}>{error || '正在加载 Repair Task…'}</p>{error && <button className={`${secondaryButton} mt-4`} onClick={() => void load()}>重试</button>}</div>
  const digest = typeof task.currentStep?.output?.artifactDigest === 'string' ? task.currentStep.output.artifactDigest : ''
  const taskView = task as RepairTaskView
  const pendingDecision = taskView.pendingDecision ?? null
  const resumeAvailable = task.resumeAvailable ?? (task.canResume === true)
  const canResumePendingDecision = Boolean(pendingDecision && taskView.canResume)
  const pendingDecisionResumeAvailable = Boolean(pendingDecision && resumeAvailable)
  const retryFailedPlanAvailable = Boolean(resumeAvailable
    && task.status === 'failed'
    && task.currentStep?.phase === 'repair_plan'
    && task.currentStep.status === 'failed')
  const canRetryFailedPlan = retryFailedPlanAvailable && taskView.canResume === true
  const canOperate = task.canOperate === true || task.canAdminOperate === true
  const adminOperator = task.canAdminOperate === true && task.canOperate !== true
  const canContinue = canOperate && task.executionSupported === true
  const ownerOnlyTitle = canOperate
    ? (adminOperator ? '管理员可推进 Repair 流程；需要 Owner 登录态的请求仍由 Owner 完成' : undefined)
    : '仅任务 Owner 可操作'
  const canReuseCurrentExecution = executionCanContinue(task)
  const showModelApiKey = task.agentMode === 'openclaw'
    && task.openclawUsesCustomApiKey
    && !(task.status === 'waiting_approval' && task.plan?.recommendation?.disposition === 'no_change')
    && (task.status === 'waiting_context'
      || (retryFailedPlanAvailable && !canReuseCurrentExecution)
      || pendingDecisionResumeAvailable
      || (['waiting_approval', 'waiting_acceptance'].includes(task.status) && !canReuseCurrentExecution))
  const agentModel = task.agentMode === 'cfuse'
    ? (task.cfuseModel || '—')
    : (task.llmUseDefault ? '默认 LLM 配置' : (task.llmModel || '—'))
  const taskStepsById = new Map(
    (taskView.steps ?? []).map((step) => [step.stepId, step]),
  )
  const stepGroups: RepairStepGroup[] = task.history.map((step) => ({
    stepId: step.stepId,
    stepNo: step.stepNo,
    phase: step.phase,
  }))
  if (task.currentStep && !stepGroups.some((step) => step.stepId === task.currentStep?.stepId)) {
    stepGroups.push({
      stepId: task.currentStep.stepId,
      stepNo: task.currentStep.stepNo,
      phase: task.currentStep.phase,
    })
  }
  const aisStepRows = [...(taskView.steps ?? [])]
  if (task.currentStep && !aisStepRows.some((step) => step.stepId === task.currentStep?.stepId)) {
    aisStepRows.push(task.currentStep)
  }
  const aisContainersByJob = new Map<string, RepairAisContainer>()
  for (const step of aisStepRows) {
    if (!step.aisJobId) continue
    const container = aisContainersByJob.get(step.aisJobId) ?? { jobId: step.aisJobId, steps: [] }
    if (!container.steps.some((item) => item.stepId === step.stepId)) {
      container.steps.push({ stepId: step.stepId, stepNo: step.stepNo, phase: step.phase, status: step.status })
      container.steps.sort((left, right) => left.stepNo - right.stepNo)
    }
    aisContainersByJob.set(step.aisJobId, container)
  }
  const aisContainers = [...aisContainersByJob.values()].sort((left, right) => (
    Math.max(...right.steps.map((step) => step.stepNo)) - Math.max(...left.steps.map((step) => step.stepNo))
  ))
  const openAisContainers = (jobId?: string | null, source: 'task' | 'step' = 'task') => {
    const targetJobId = jobId || task.currentStep?.aisJobId || aisContainers[0]?.jobId
    if (!targetJobId) return
    aisDrawerTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setSelectedAisSource(source)
    setSelectedAisJobId(targetJobId)
  }
  const currentStepIsArchived = Boolean(task.currentStep
    && task.history.some((step) => step.stepId === task.currentStep?.stepId))
  const currentStepAwaitingAisJob = Boolean(task.currentStep
    && !task.currentStep.aisJobId
    && ['created', 'pending', 'dispatching', 'dispatched', 'running', 'executing'].includes(task.currentStep.status))
  const referencedPlanStep = task.plan?.stepId
    ? taskStepsById.get(task.plan.stepId)
    : undefined
  const referencedCurrentPlanStep = task.plan?.stepId
    && task.currentStep?.stepId === task.plan.stepId
    && task.currentStep.phase === 'repair_plan'
    ? task.currentStep
    : undefined
  const planStepSummary = referencedCurrentPlanStep?.summary?.trim()
    || (referencedPlanStep?.phase === 'repair_plan' ? referencedPlanStep.summary?.trim() : null)
  const currentPlanError = task.currentStep?.phase === 'repair_plan'
    ? task.currentStep.error
    : null
  const currentPlanFailure = task.currentStep?.phase === 'repair_plan'
    ? task.currentStep.failure
    : null
  const planDisposition = task.plan?.recommendation?.disposition
  const planIsNoChange = planDisposition === 'no_change'
  const planIsInsufficient = planDisposition === 'insufficient_evidence'
  const planIsLegacyEmpty = Boolean(task.plan
    && task.plan.actions.length === 0
    && !task.plan.recommendation)
  const planNeedsReplan = planIsInsufficient || planIsLegacyEmpty

  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve')} className="mb-5 text-sm text-gray-500 hover:text-gray-800">‹ 返回任务列表</button>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div><div className="flex flex-wrap items-center gap-2"><StatusPill status={task.status} phase={task.currentStep?.phase} /><span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-700">Bot 修复</span><span className="font-mono text-xs text-gray-400">{task.taskId}</span></div><h1 ref={taskHeadingRef} tabIndex={-1} className="mt-3 text-2xl font-semibold text-gray-950">{task.taskName || `Repair ${task.botId}`}</h1>{task.issue && <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-gray-600">{task.issue.symptom}</p>}</div>
        <div className="flex flex-wrap items-center gap-2">{task.shared && <span className="rounded-full bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700">已公开分享</span>}{!['completed', 'failed', 'canceled'].includes(task.status) && <button type="button" disabled={!task.canTerminate || Boolean(busyAction) || sharingBusy} title={!canOperate ? '仅任务 Owner 可操作' : undefined} className={dangerButton} onClick={() => void terminate()}>{busyAction === 'terminate' ? '正在终止…' : '终止实验'}</button>}<button type="button" disabled={!task.canManageShare || sharingBusy || Boolean(busyAction)} title={!task.canManageShare ? '仅任务 Owner 可操作' : undefined} className={secondaryButton} onClick={() => void toggleSharing()}>{sharingBusy ? '更新中…' : task.shared ? '关闭分享' : '分享'}</button></div>
      </header>

      {task.insightSource && <section className="mt-5 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 className="text-sm font-semibold text-indigo-950">来自效果中心的改进项</h2><p className="mt-1 text-xs leading-5 text-indigo-800">问题描述、Session 和冻结证据已作为本次 Repair 的输入；Agent 会优先读取完整 Evidence 后再生成方案。</p></div>
          <span className="rounded-full bg-white px-2.5 py-1 font-mono text-[10px] text-indigo-700">#{task.insightSource.improvementId}</span>
        </div>
        <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
          <div className="rounded-lg bg-white/80 px-3 py-2.5"><p className="text-[10px] text-indigo-600/70">Evidence</p><p className="mt-1 font-medium text-indigo-950">{task.insightSource.evidenceCount} 条 · {task.insightSource.sessionIds.length} 个 Session</p></div>
          <div className="rounded-lg bg-white/80 px-3 py-2.5"><p className="text-[10px] text-indigo-600/70">授权方式</p><p className="mt-1 font-medium text-indigo-950">{task.insightSource.authorizationMode === 'PERSISTENT' ? '持续授权' : '仅本次授权'}</p></div>
        </div>
        {task.insightSource.repairDirection && <div className="mt-3 rounded-lg bg-white/80 px-3 py-2.5 text-xs leading-5 text-indigo-950"><span className="font-medium">修复方向：</span>{task.insightSource.repairDirection}</div>}
      </section>}

      {adminOperator && <div role="status" className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">当前为管理员操作视角。你可以推进方案、结果验收、恢复或终止该 Repair；需要 Owner 浏览器登录态的上下文请求和 CodeFuse 授权仍需由 Owner 完成。</div>}
      {!canOperate && <div role="status" className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">这是共享的只读 Repair 任务。任务内容与 Owner 视图一致；审批、恢复、登录授权、终止和分享设置等操作仅任务 Owner 可用，相关控件已禁用。</div>}
      {!task.executionSupported && <div role="alert" className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><p className="font-medium">历史执行器已停用</p><p className="mt-1 text-xs leading-5">{task.executionBlock?.message || '该任务只能查看已有信息，不能继续执行。'}</p></div>}

      {(pendingContextCalls.length > 0 || relayNotice) && task.status === 'running' && <div role="status" className="mt-5 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800"><p className="font-medium">正在补充 Context</p><p className="mt-1 text-xs leading-5">{relayNotice || (ownerCanOperate ? '页面会自动使用当前登录态完成受控运行态或 OCB 请求，AIS 容器保持运行。' : '任务正在等待 Owner 页面使用登录态完成受控请求，AIS 容器保持运行。')}</p></div>}
      {cfuseLoginCall && <section className="mt-5 rounded-2xl border border-blue-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-base font-semibold text-gray-900">登录 CodeFuse</h2><p className="mt-1 text-xs leading-5 text-gray-500">当前 AIS 容器正在等待 cfuse 授权。完成授权后，组件会把 AuthCode 写回同一个 cfuse 进程。</p></div><StatusPill status={cfuseLoginCall.status} /></div>
        {cfuseLoginCall.status === 'pending' && <div className="mt-4 space-y-4">
          {cfuseLoginUrl && ownerCanOperate ? <a className="inline-flex text-sm font-medium text-blue-600 hover:text-blue-700" href={cfuseLoginUrl} target="_blank" rel="noreferrer">打开 CodeFuse 授权页 ↗</a> : <p className="text-sm text-amber-700">{ownerCanOperate ? '登录链接尚未返回，请稍后刷新。' : 'CodeFuse 登录入口仅任务 Owner 可用。'}</p>}
          <label className="block text-xs font-medium text-gray-600">AuthCode<input aria-label="CodeFuse AuthCode" type="password" autoComplete="off" disabled={!ownerCanOperate} className={`${inputClass} mt-1.5 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400`} value={cfuseAuthCode} onChange={(event) => setCfuseAuthCode(event.target.value)} placeholder="粘贴授权页面返回的一次性 AuthCode" /></label>
          <button disabled={!ownerCanOperate || cfuseLoginBusy || !cfuseAuthCode.trim()} title={ownerOnlyTitle} className={primaryButton} onClick={() => void submitCfuseAuthCode()}>{cfuseLoginBusy ? '正在提交…' : '提交 AuthCode'}</button>
        </div>}
        {cfuseLoginCall.status === 'executing' && <p role="status" className="mt-4 rounded-lg bg-blue-50 p-3 text-sm text-blue-800">AuthCode 已提交，容器正在完成 cfuse 授权。</p>}
        {cfuseLoginCall.status === 'succeeded' && <p role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">cfuse 已完成授权，正在继续 Repair。</p>}
        {['failed', 'canceled', 'unknown'].includes(cfuseLoginCall.status) && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">cfuse 授权未完成，请重新启动当前 Repair execution 后再登录。</p>}
        {cfuseLoginError && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{cfuseLoginError}</p>}
      </section>}
      {(error || decisionError) && <p role="alert" className="mt-5 rounded-lg bg-red-50 p-3 text-sm text-red-700">{decisionError || error}</p>}
      <CurrentStepRecoveryAlert step={task.currentStep} />
      <CurrentStepFailureAlert step={task.currentStep} showRetry={retryFailedPlanAvailable} canRetry={canRetryFailedPlan} retrying={busyAction === 'resume'} onRetry={() => void resume()} />

      <div className="mt-6 grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,320px)]">
        <main className="min-w-0 space-y-5">
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-gray-900">Repair 工作流</h2><p className="mt-1 text-xs text-gray-500">先生成方案，批准后才执行修复；每个步骤都保留独立审计记录。</p></div><div className="flex items-center gap-2"><button ref={taskAisButtonRef} type="button" className={secondaryButton} disabled={aisContainers.length === 0} onClick={() => openAisContainers()}>{aisContainers.length > 0 ? `查看 AIS 容器（${aisContainers.length}）` : currentStepAwaitingAisJob ? 'AIS 容器分配中' : '暂无 AIS 容器'}</button><StatusPill status={task.status} phase={task.currentStep?.phase} /></div></div>
            <div className="mt-5 space-y-3">
              {task.history.map((step) => {
                const persistedStep = taskStepsById.get(step.stepId)
                const canViewHistoricalPlan = step.phase === 'repair_plan'
                  && step.status === 'succeeded'
                  && Boolean(step.artifactDigest)
                const historicalPlanExpanded = expandedHistoricalPlans[step.stepId] === true
                const historicalPlan = historicalPlans[step.stepId]
                const stepReason = repairErrorMessage(persistedStep?.error)
                  || (step.status === 'interrupted' ? '该步骤已中断，服务端未返回更具体原因。' : '')
                const reasonLabel = step.status === 'interrupted' ? '中断原因' : '失败原因'
                const reasonClass = step.status === 'interrupted'
                  ? 'bg-orange-50 text-orange-800'
                  : 'bg-red-50 text-red-700'
                return <div key={step.stepId} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-medium text-gray-900">{step.phase === 'repair_plan' ? '方案步骤' : '执行步骤'} · 第 {step.stepNo} 步</p><p className="mt-1 font-mono text-[10px] text-gray-400">{step.stepId}</p><div className="mt-2 flex flex-wrap gap-3">{persistedStep?.aisJobId && <button type="button" aria-label={`查看第 ${step.stepNo} 步关联的 AIS 容器`} className="text-xs font-medium text-blue-600 hover:text-blue-700" onClick={() => openAisContainers(persistedStep.aisJobId, 'step')}>查看关联容器</button>}{canViewHistoricalPlan && <button type="button" aria-expanded={historicalPlanExpanded} aria-controls={`repair-history-plan-${encodeURIComponent(step.stepId)}`} className="text-xs font-medium text-blue-600 hover:text-blue-700" onClick={() => void toggleHistoricalPlan(step.stepId)}>{historicalPlanExpanded ? `收起第 ${step.stepNo} 步方案` : `查看第 ${step.stepNo} 步方案`}</button>}</div></div><StatusPill status={step.status} phase={step.phase} /></div>
                  {stepReason && <div className={`mt-3 rounded-lg p-3 text-xs ${reasonClass}`}><p>{reasonLabel}：{stepReason}</p><RepairFailureDetails failure={persistedStep?.failure} /></div>}
                  {!stepReason && persistedStep?.summary && <p className="mt-3 text-sm text-gray-700">{persistedStep.summary}</p>}
                  {step.feedback && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">用户反馈：{step.feedback}</p>}
                  {historicalPlanExpanded && <div id={`repair-history-plan-${encodeURIComponent(step.stepId)}`} data-historical-plan className="mt-4">
                    {historicalPlan?.status === 'loaded'
                      ? <PlanPanel
                          plan={historicalPlan.value.plan}
                          defaultExpanded
                          title={`第 ${step.stepNo} 步方案详情`}
                          description="历史方案只读，仅用于审计，不能批准或执行。"
                          idPrefix={`history-${step.stepId}`}
                        />
                      : historicalPlan?.status === 'error'
                        ? <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"><p className="font-medium">历史方案加载失败</p><p className="mt-1 text-xs leading-5">{historicalPlan.message}</p><p className="mt-1 text-xs leading-5">请收起后重新展开再试。</p></div>
                        : <p role="status" className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800">正在加载第 {step.stepNo} 步历史方案…</p>}
                  </div>}
                </div>
              })}
              {task.currentStep && !currentStepIsArchived && <div className="rounded-xl border border-blue-200 bg-blue-50/40 p-4">
                <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-medium text-gray-900">{task.currentStep.phase === 'repair_plan' ? '方案步骤' : '执行步骤'} · 第 {task.currentStep.stepNo} 步</p><p className="mt-1 font-mono text-[10px] text-gray-400">{task.currentStep.stepId}</p>{task.currentStep.aisJobId ? <button type="button" aria-label={`查看第 ${task.currentStep.stepNo} 步关联的 AIS 容器`} className="mt-2 text-xs font-medium text-blue-600 hover:text-blue-700" onClick={() => openAisContainers(task.currentStep?.aisJobId, 'step')}>查看关联容器</button> : <span data-step-container-status className="mt-2 inline-flex text-xs text-gray-500">{currentStepAwaitingAisJob ? 'AIS 容器分配中' : '未关联 AIS 容器'}</span>}</div><StatusPill status={task.currentStep.status} phase={task.currentStep.phase} /></div>
                {task.currentStep.summary && <p className="mt-3 text-sm text-gray-700">{task.currentStep.summary}</p>}
                {task.currentStep.error && (task.currentStep.status === 'failed'
                  ? <p className="mt-3 text-xs leading-5 text-red-700">本步骤未完成，详细原因和重试入口见页面上方错误卡。</p>
                  : <div className="mt-3 rounded-lg bg-orange-50 p-3 text-xs text-orange-800"><p>中断原因：{repairErrorMessage(task.currentStep.error)}</p><RepairFailureDetails failure={task.currentStep.failure} /></div>)}
              </div>}
            </div>
          </section>

          {(task.currentStep?.phase === 'repair_plan' || task.plan) && <PlanPanel key={`${task.plan?.stepId ?? task.currentStep?.stepId ?? 'plan'}:${task.status}:${Boolean(task.plan)}`} plan={task.plan} stepSummary={planStepSummary} stepStatus={task.currentStep?.status} stepError={currentPlanError} stepFailure={currentPlanFailure} defaultExpanded={Boolean(task.plan && ['running', 'waiting_approval', 'waiting_acceptance'].includes(task.status))} failurePresented={task.currentStep?.status === 'failed'} />}
          {task.executionSupported && task.status === 'waiting_approval' && !pendingDecision && <section data-plan-decision className="rounded-2xl border border-amber-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900">方案决策</h2>
            <p className="mt-2 text-xs leading-5 text-gray-500">{planIsNoChange
              ? '当前方案明确建议不执行写操作，请结合方案结论和诊断依据确认是否结束本次 Repair。'
              : planNeedsReplan
                ? '当前方案不能批准执行或确认无需修复，请补充线索后重新规划。'
                : '请先核对上方方案中的准确命令、风险、验证方式与回滚方案，再决定是否执行。'}</p>
            {planIsNoChange && <p className="mt-2 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-700">确认后任务将直接完成，不会创建或进入 Apply 步骤。</p>}
            {planIsInsufficient && <p role="alert" className="mt-2 rounded-lg bg-orange-50 p-3 text-xs leading-5 text-orange-800">Agent 明确判断现有证据不足，不能把该方案批准为“无需修复”。</p>}
            {planIsLegacyEmpty && <p role="alert" className="mt-2 rounded-lg bg-orange-50 p-3 text-xs leading-5 text-orange-800">旧版空方案未声明结论类型，不能安全判断为无需修复。</p>}
            {!task.plan && <p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">方案内容尚未加载，暂不能批准执行。请刷新后重试。</p>}
            <p className="mt-2 break-all font-mono text-[10px] text-gray-400">Digest: {digest || '尚未返回'}</p>
            {activeFeedbackIntent === 'reject_plan'
              ? <div className="mt-4"><label className="block text-xs font-medium text-gray-600">驳回原因<textarea aria-label="驳回方案原因" disabled={!canOperate} className={`${inputClass} mt-1.5 min-h-24 resize-y disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400`} maxLength={4000} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="说明需要补充的证据、方案中不准确或需要重新调查的内容" /></label><div className="mt-3 flex flex-wrap gap-2"><button disabled={!canContinue || Boolean(busyAction) || !feedback.trim()} title={ownerOnlyTitle} className={primaryButton} onClick={() => void decidePlan('reject')}>{busyAction === 'reject' ? '正在提交…' : '提交并重新规划'}</button><button disabled={!canOperate || Boolean(busyAction)} title={ownerOnlyTitle} className={secondaryButton} onClick={() => { setFeedbackIntent(null); setFeedbackContext(''); setFeedback(''); setDecisionError('') }}>取消</button></div></div>
              : <div className="mt-4 flex flex-wrap gap-2">
                {planNeedsReplan
                  ? <button disabled={!canContinue || Boolean(busyAction)} title={ownerOnlyTitle} className={primaryButton} onClick={() => { setFeedback(''); setFeedbackContext(decisionContext); setFeedbackIntent('reject_plan'); setDecisionError('') }}>补充线索并重新规划</button>
                  : <button disabled={!canContinue || Boolean(busyAction) || !digest || !task.plan} title={ownerOnlyTitle} className={primaryButton} onClick={() => void decidePlan('approve')}>{busyAction === 'approve' ? (planIsNoChange ? '正在确认…' : '正在批准…') : (planIsNoChange ? '确认不执行修复并结束' : '批准并执行')}</button>}
                {!planNeedsReplan && <button disabled={!canContinue || Boolean(busyAction)} title={ownerOnlyTitle} className={secondaryButton} onClick={() => { setFeedback(''); setFeedbackContext(decisionContext); setFeedbackIntent('reject_plan'); setDecisionError('') }}>驳回方案</button>}
              </div>}
          </section>}
          {(task.currentStep?.phase === 'repair_apply' || task.applyResult) && <ApplyResultPanel value={task.applyResult ?? task.currentStep?.output ?? {}} plan={task.plan} running={task.currentStep?.phase === 'repair_apply' && task.currentStep.status === 'running'} />}
          <ToolCallsPanel
            calls={task.toolCalls ?? []}
            steps={stepGroups}
            currentStepId={task.currentStep?.stepId}
            currentPhase={task.currentStep?.phase}
            approvedPlanStepId={task.approvedPlan?.stepId}
            plan={task.plan}
            truncated={task.toolCallAuditTruncated}
          />
        </main>

        <aside className="min-w-0 space-y-4">
          {showModelApiKey && <section className="rounded-2xl border border-blue-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">新 AIS Job 的模型 Token</h2><p className="mt-2 text-xs leading-5 text-gray-500">该任务最初使用了自定义 Token。Token 不会从任务中恢复；只有当前容器无法继续、需要拉起新 Job 时才会再次发送。</p><label className="mt-4 block text-xs font-medium text-gray-600">Token<input aria-label="新 AIS Job 模型 Token" type="password" autoComplete="new-password" disabled={!canOperate} className={`${inputClass} mt-1.5 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400`} value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} placeholder="新 Job 启动前重新输入" /></label></section>}
          {task.executionSupported && pendingDecision && ['waiting_approval', 'waiting_acceptance'].includes(task.status) && <section className="rounded-2xl border border-blue-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">决策已提交</h2><p className="mt-2 text-xs leading-5 text-gray-500">{pendingDecisionResumeAvailable ? '原 AIS 容器已结束；启动新容器后会从历史产物继续执行已提交的决策。' : '当前 AIS 容器正在领取决策并继续处理，请稍候。'}</p>{pendingDecisionResumeAvailable && <button disabled={!canResumePendingDecision || Boolean(busyAction)} title={ownerOnlyTitle} className={`${primaryButton} mt-4 w-full`} onClick={() => void resume()}>{busyAction === 'resume' ? '正在启动…' : '启动新容器继续'}</button>}</section>}
          {task.status === 'waiting_acceptance' && !pendingDecision && <section className="rounded-2xl border border-violet-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">结果确认</h2><p className="mt-2 text-xs leading-5 text-gray-500">{!task.executionSupported ? '当前执行器已停用；你仍可采纳已有结果，但不能继续修复。' : canReuseCurrentExecution ? `当前 AIS 容器会等待至 ${formatTime(task.execution?.decisionDeadlineAt)}；选择“仍未修好”将复用当前 Job 和 Agent 会话。` : '等待窗口已结束。采纳结果不受影响；选择“仍未修好”将启动新 AIS 容器并从历史产物恢复。'}</p>{activeFeedbackIntent === 'retry_result' ? <div className="mt-4"><label className="block text-xs font-medium text-gray-600">未解决的问题<textarea aria-label="继续修复反馈" disabled={!canOperate} className={`${inputClass} mt-1.5 min-h-24 resize-y disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400`} maxLength={4000} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="说明仍未解决的现象，Agent 会据此重新规划" /></label><div className="mt-3 grid gap-2"><button disabled={!canContinue || Boolean(busyAction) || !feedback.trim()} title={ownerOnlyTitle} className={primaryButton} onClick={() => void decideResult('retry')}>{busyAction === 'retry' ? '正在提交…' : '提交反馈并继续'}</button><button disabled={!canOperate || Boolean(busyAction)} title={ownerOnlyTitle} className={secondaryButton} onClick={() => { setFeedbackIntent(null); setFeedbackContext(''); setFeedback(''); setDecisionError('') }}>取消</button></div></div> : <div className="mt-4 grid gap-2"><button disabled={!canOperate || Boolean(busyAction)} title={ownerOnlyTitle} className={primaryButton} onClick={() => void decideResult('accept')}>{busyAction === 'accept' ? '正在确认…' : '采纳修复结果'}</button>{task.executionSupported && <button disabled={!canContinue || Boolean(busyAction)} title={ownerOnlyTitle} className={secondaryButton} onClick={() => { setFeedback(''); setFeedbackContext(decisionContext); setFeedbackIntent('retry_result'); setDecisionError('') }}>仍未修好，继续</button>}</div>}</section>}
          {task.executionSupported && task.status === 'waiting_context' && <section className="rounded-2xl border border-orange-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">恢复上下文收集</h2><p className="mt-2 text-xs leading-5 text-gray-500">此前浏览器未能及时履行 OCB 请求，旧容器已释放。恢复后会创建新的 Step 和 AIS 容器；新容器需要登录 cfuse 时，本页面会显示授权入口。</p><button disabled={!canContinue || Boolean(busyAction)} title={ownerOnlyTitle} className={`${primaryButton} mt-4 w-full`} onClick={() => void resume()}>{busyAction === 'resume' ? '正在恢复…' : '恢复 Repair'}</button></section>}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">修复对象与配置</h2><p className="mt-1 text-[11px] leading-5 text-gray-500">这里仅展示贯穿整个任务的固定信息；步骤与 AIS 容器关系在工作流的容器抽屉中查看。</p><dl className="mt-4 space-y-1"><DetailRow label="Bot" value={task.botId} mono /><DetailRow label="环境" value={task.targetEnvironment} /><DetailRow label="诊断权限" value={task.diagnosticMode === 'deep' ? '深度诊断 Shell' : '广泛只读观察'} /><DetailRow label="执行器" value={task.agentMode === 'cfuse' ? 'cfuse' : 'OpenClaw'} /><DetailRow label="模型" value={agentModel} />{task.agentMode === 'cfuse' ? <DetailRow label="cfuse Engine" value={task.cfuseEngine || '—'} /> : <DetailRow label="Token" value={task.openclawUsesCustomApiKey ? '本次使用自定义 Token（不展示）' : '使用 Snapshot 默认配置'} />}<DetailRow label="创建时间" value={formatTime(task.createdAt)} /><DetailRow label="更新时间" value={formatTime(task.updatedAt)} /></dl>{task.error && <p className="mt-4 rounded-lg bg-red-50 p-3 text-xs text-red-700">{task.error}</p>}</section>
          {task.issue && <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="text-sm font-semibold text-gray-900">输入线索</h2><dl className="mt-4 space-y-1"><DetailRow label="Trace ID" value={task.issue.traceId || '—'} mono /><DetailRow label="相关任务" value={task.issue.relatedTaskId || '—'} mono /><DetailRow label="时间范围" value={`${formatTime(task.issue.timeRange.from)} 至 ${formatTime(task.issue.timeRange.to)}`} /></dl>{task.issue.errorText && <pre className="mt-4 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-700">{task.issue.errorText}</pre>}</section>}
        </aside>
      </div>
      {selectedAisJobId && aisContainers.length > 0 && <AisContainersDrawer containers={aisContainers} selectedJobId={selectedAisJobId} selectedFromStep={selectedAisSource === 'step'} currentJobId={task.currentStep?.aisJobId} execution={task.execution} onClose={closeAisContainers} />}
    </div>
  )
}

export default function Repair({ view }: { view: 'create' | 'detail' }) {
  const [modelApiKey, setModelApiKey] = useState('')
  return view === 'create'
    ? <CreateRepair modelApiKey={modelApiKey} setModelApiKey={setModelApiKey} />
    : <RepairDetail modelApiKey={modelApiKey} setModelApiKey={setModelApiKey} />
}
