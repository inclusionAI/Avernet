import { useState, useEffect, useCallback } from 'react'
import StatusBadge from './StatusBadge'
import { formatTime, formatDuration } from '@avernet/workflow/web/utils/time'
import { approvalDisplay, type ApprovalDisplay } from '../../shared/approval-display'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import type { ApprovalCardSummary } from '@avernet/clawweb-shared/web/api/client'

// ── Types ──────────────────────────────────────────────────────────────

type ApprovalStatus = 'pending' | 'approved' | 'rejected'

type CardField = { label: string; value: string }

type SectionFieldAction = {
  key: string
  label: string
  type?: 'primary' | 'default' | 'danger'
  autoFill?: string
}

type SectionField = {
  id: string
  label: string
  expected?: string
  expectedLabel?: string
  actual?: string
  actualLabel?: string
  actions?: SectionFieldAction[]
  customizable?: boolean
  placeholder?: string
}

type CardSection = {
  id: string
  title: string
  description?: string
  style?: 'default' | 'warning' | 'info' | 'danger' | 'success'
  icon?: string
  fields: SectionField[]
}

type SectionState = Record<string, { action: string; value?: string }>

type ApprovalData = {
  id: number
  flowId: string
  nodeId: string
  workflowId: string
  workflowTitle: string | null
  approvalType: string | null
  message: string | null
  cardFields: CardField[]
  display?: ApprovalDisplay
  sections?: CardSection[]
  approverIds: string[]
  approverNames: string[]
  approvalPolicy: string
  approvedBy: string[]
  rejectedBy: string[]
  status: ApprovalStatus
  deliveryMode: string
  createdAt: number
  resolvedAt: number | null
  comment?: string | null
  note?: string | null
  detail?: Record<string, unknown>
  isApprover?: boolean
}

type ResolveResult = {
  ok?: boolean
  action?: string
  status?: ApprovalStatus
  approvedBy?: string[]
  rejectedBy?: string[]
  comment?: string | null
  error?: string
  message?: string
}

// ── API helpers ────────────────────────────────────────────────────────

async function fetchApproval(id: number, empId?: string): Promise<ApprovalData> {
  const url = empId
    ? `/api/approval/${id}?empId=${encodeURIComponent(empId)}`
    : `/api/approval/${id}`
  const res = await fetch(url)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.message || `HTTP ${res.status}`)
  }
  return res.json()
}

async function resolveApproval(
  id: number,
  action: 'approve' | 'reject',
  empId: string,
  comment?: string,
  detail?: Record<string, unknown>,
): Promise<ResolveResult> {
  const res = await fetch(`/api/approval/${id}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ empId, action, comment, detail }),
  })
  return res.json()
}

// ── Validation ─────────────────────────────────────────────────────────

function validateSections(sections: CardSection[], selection: Record<string, SectionState>): string | null {
  for (const section of sections) {
    for (const field of section.fields) {
      if (!field.actions?.length && !field.customizable) continue
      if (field.actions?.length && !selection[section.id]?.[field.id]?.action) {
        return `「${section.title}」中「${field.label}」请选择处理意见`
      }
      if (
        field.customizable &&
        selection[section.id]?.[field.id]?.action === 'customize' &&
        !selection[section.id][field.id]?.value?.trim()
      ) {
        return `「${section.title}」中「${field.label}」请填写自定义内容`
      }
    }
  }
  return null
}

function buildDetailPayload(
  sections: CardSection[],
  selection: Record<string, SectionState>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const section of sections) {
    const sectionData: Record<string, unknown> = {}
    for (const field of section.fields) {
      const sel = selection[section.id]?.[field.id]
      if (sel) {
        const actionDef = field.actions?.find((a) => a.key === sel.action)
        let derivedValue = sel.value ?? actionDef?.autoFill ?? (
          sel.action === 'accept' ? field.expected
          : sel.action === 'keep' ? field.actual
          : null
        )
        if (sel.action === 'customize' && typeof derivedValue === 'string') {
          const trimmed = derivedValue.trim()
          if (!trimmed) derivedValue = actionDef?.autoFill || field.expected || null
        }
        sectionData[field.id] = {
          action: sel.action,
          actionLabel: actionDef?.label ?? sel.action,
          value: derivedValue,
          expected: field.expected ?? null,
          actual: field.actual ?? null,
        }
      }
    }
    if (Object.keys(sectionData).length > 0) payload[section.id] = sectionData
  }
  return payload
}

// ── Component ──────────────────────────────────────────────────────────

interface ApprovalSlidePanelProps {
  card: ApprovalCardSummary
  run: FlowRun
  onClose: () => void
  onResolved?: () => void
}

export default function ApprovalSlidePanel({ card, run, onClose, onResolved }: ApprovalSlidePanelProps) {
  const [data, setData] = useState<ApprovalData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [comment, setComment] = useState('')
  const [result, setResult] = useState<ResolveResult | null>(null)
  const [sectionSelection, setSectionSelection] = useState<Record<string, SectionState>>({})
  const [validationError, setValidationError] = useState<string | null>(null)

  const isSectionMode = !!data?.sections && data.sections.length > 0

  // Resolve current user's empId from clawweb auth context
  const empId = getClientUser()?.userId ?? ''

  // Load approval detail
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchApproval(card.id, empId || undefined)
      .then((d) => {
        if (cancelled) return
        setData(d)
        setLoading(false)
        if (d.detail && d.sections && d.status !== 'pending') {
          const initial: Record<string, SectionState> = {}
          for (const section of d.sections) {
            const secDetail = d.detail[section.id] as Record<string, unknown> | undefined
            if (secDetail) {
              initial[section.id] = {}
              for (const field of section.fields) {
                const fd = secDetail[field.id] as Record<string, unknown> | undefined
                if (fd) {
                  initial[section.id][field.id] = {
                    action: (fd.actionLabel as string) ?? '',
                    value: (fd.value as string) ?? undefined,
                  }
                }
              }
            }
          }
          setSectionSelection(initial)
        }
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '加载失败')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [card.id, empId])

  // Poll for status updates when pending
  useEffect(() => {
    if (!data || data.status !== 'pending') return
    const timer = setInterval(async () => {
      try {
        const fresh = await fetchApproval(card.id, empId || undefined)
        setData(fresh)
      } catch { /* ignore poll errors */ }
    }, 5000)
    return () => clearInterval(timer)
  }, [card.id, data?.status, empId])

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const handleAction = useCallback(
    async (action: 'approve' | 'reject') => {
      if (!data) return

      if (!empId) {
        setResult({ error: '无法获取用户身份，请先登录' })
        return
      }

      if (isSectionMode && data.sections) {
        const err = validateSections(data.sections, sectionSelection)
        if (err) { setValidationError(err); return }
        setValidationError(null)
      }

      setActionLoading(true)
      try {
        const detail = isSectionMode && data.sections
          ? buildDetailPayload(data.sections, sectionSelection)
          : undefined
        const res = await resolveApproval(card.id, action, empId, comment || undefined, detail)
        setResult(res)
        if (res.ok) {
          const fresh = await fetchApproval(card.id, empId)
          setData(fresh)
          onResolved?.()
        }
      } catch (err) {
        setResult({ error: err instanceof Error ? err.message : '操作失败' })
      } finally {
        setActionLoading(false)
      }
    },
    [card.id, data, comment, isSectionMode, sectionSelection, onResolved, empId],
  )

  const copy = data ? approvalDisplay(data.approvalType, data.display) : null
  const isResolved = data?.status === 'approved' || data?.status === 'rejected'
  const canAct = data?.status === 'pending'

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-200 bg-gradient-to-r from-amber-500 to-orange-500 px-6 py-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-lg">📋</span>
              <h2 className="font-bold text-white text-lg">审批</h2>
            </div>
            <div className="mt-1 flex items-center gap-3">
              <span className="font-mono text-white/70 text-xs">{run.flow_id}</span>
              <StatusBadge status={run.status} />
            </div>
            <div className="mt-1 flex items-center gap-4 text-white/60 text-xs">
              <span>{run.workflow_title || run.workflow_id}</span>
              {data && <span>节点: {data.nodeId}</span>}
            </div>
          </div>
          <button
            onClick={onClose}
            className="ml-4 rounded-md p-1.5 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
            title="关闭"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Loading */}
          {loading && (
            <div className="flex flex-col items-center py-16 text-gray-400">
              <div className="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
              <p className="text-sm">加载审批信息...</p>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
              <span className="font-medium text-red-800 text-sm">加载失败</span>
              <p className="mt-1 text-red-700 text-sm">{error}</p>
            </div>
          )}

          {/* Approval content */}
          {data && copy && (
            <>
              {/* Status badge */}
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${
                  data.status === 'pending' ? 'bg-amber-50 text-amber-700 border-amber-200'
                  : data.status === 'approved' ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : 'bg-red-50 text-red-700 border-red-200'
                }`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${
                    data.status === 'pending' ? 'bg-amber-400 animate-pulse'
                    : data.status === 'approved' ? 'bg-emerald-500'
                    : 'bg-red-500'
                  }`} />
                  {data.status === 'pending' ? '待审批' : data.status === 'approved' ? '已通过' : '已拒绝'}
                </span>
                <span className="text-sm font-medium text-slate-700">{copy.subtitle}</span>
              </div>

              {/* Title */}
              <h3 className="text-base font-semibold text-slate-800">
                {copy.title ?? (data.message || data.workflowTitle || '审批请求')}
              </h3>

              {/* Message */}
              {data.message && (
                <p className="text-sm text-slate-600 leading-relaxed">{data.message}</p>
              )}

              {/* Card fields (legacy mode) */}
              {!isSectionMode && data.cardFields.length > 0 && (
                <div className="rounded-xl border border-slate-200 overflow-hidden">
                  {data.cardFields.map((field, i) => (
                    <div key={i} className={`px-4 py-3 ${i < data.cardFields.length - 1 ? 'border-b border-slate-100' : ''}`}>
                      <div className="text-xs text-slate-400 font-medium mb-1">{field.label}</div>
                      <div className="text-sm text-slate-700 break-words whitespace-pre-wrap">{field.value || '—'}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Section mode */}
              {isSectionMode && data.sections && (
                <div className="space-y-3">
                  {data.sections.map((section) => (
                    <SectionCard
                      key={section.id}
                      section={section}
                      selection={sectionSelection}
                      setSelection={setSectionSelection}
                      disabled={isResolved || actionLoading}
                    />
                  ))}
                </div>
              )}

              {/* Approver info */}
              <div className="rounded-xl border border-slate-200 overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-100">
                  <div className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-2">审批人</div>
                  <div className="flex flex-wrap gap-2">
                    {data.approverIds.map((id, i) => {
                      const name = data.approverNames[i] || id
                      const approved = data.approvedBy.includes(id)
                      const rejected = data.rejectedBy.includes(id)
                      return (
                        <span key={id} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                          approved ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : rejected ? 'bg-red-50 text-red-700 border border-red-200'
                          : 'bg-slate-100 text-slate-600 border border-transparent'
                        }`}>
                          {approved && '✓ '}
                          {rejected && '✕ '}
                          {name}
                        </span>
                      )
                    })}
                  </div>
                </div>
                <div className="px-4 py-3 border-b border-slate-100 flex justify-between text-xs">
                  <span className="text-slate-400">审批策略</span>
                  <span className="text-slate-700 font-medium">
                    {data.approvalPolicy === 'any' ? '任一审批人通过即可'
                    : data.approvalPolicy === 'all' ? '需全部审批人通过'
                    : data.approvalPolicy === 'majority' ? '多数审批人通过即可'
                    : data.approvalPolicy}
                  </span>
                </div>
                <div className="px-4 py-3 flex justify-between text-xs">
                  <span className="text-slate-400">发起时间</span>
                  <span className="text-slate-700">{formatTime(data.createdAt)}</span>
                </div>
                {data.resolvedAt && (
                  <div className="px-4 py-3 border-t border-slate-100 flex justify-between text-xs">
                    <span className="text-slate-400">{data.status === 'approved' ? '通过时间' : '拒绝时间'}</span>
                    <span className="text-slate-700">{formatTime(data.resolvedAt)}</span>
                  </div>
                )}
              </div>

              {/* Validation error */}
              {validationError && isSectionMode && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 flex items-center gap-2">
                  <span className="text-sm">⚠️</span>
                  <span className="text-red-700 text-sm">{validationError}</span>
                </div>
              )}

              {/* Result message */}
              {result && (
                <div className={`rounded-lg border px-4 py-3 flex items-center gap-2 ${
                  result.error ? 'border-red-200 bg-red-50 text-red-700'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                }`}>
                  <span>{result.error ? '⚠️' : '✓'}</span>
                  <span className="text-sm">
                    {result.error
                      ? `操作失败: ${result.error}`
                      : result.status === 'approved' ? '审批已通过' : '审批已拒绝'}
                  </span>
                </div>
              )}

              {/* Resolved status */}
              {isResolved && (
                <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-6 text-center">
                  <div className={`mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full text-2xl font-bold ${
                    data.status === 'approved' ? 'bg-emerald-100 text-emerald-600' : 'bg-red-100 text-red-600'
                  }`}>
                    {data.status === 'approved' ? '✓' : '✕'}
                  </div>
                  <div className={`text-base font-semibold ${data.status === 'approved' ? 'text-emerald-700' : 'text-red-700'}`}>
                    {data.status === 'approved' ? copy.approvedText : copy.rejectedText}
                  </div>
                  {isSectionMode && data.detail && (
                    <div className="mt-4 text-left">
                      {data.sections?.map((section) => {
                        const secDetail = data.detail?.[section.id] as Record<string, { actionLabel?: string; value?: string | null }> | undefined
                        if (!secDetail) return null
                        return (
                          <div key={section.id} className="mb-2">
                            <div className="text-sm font-medium text-slate-700">{section.title}</div>
                            {section.fields.map((field) => {
                              const fd = secDetail[field.id]
                              if (!fd) return null
                              return (
                                <div key={field.id} className="text-xs text-slate-500 pl-3 mt-1">
                                  {field.label}: <span className="text-slate-700 font-medium">{fd.actionLabel}</span>
                                  {fd.value ? ` → ${fd.value}` : ''}
                                </div>
                              )
                            })}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer Actions */}
        {data && canAct && !result?.ok && (
          <div className="border-t border-gray-200 bg-gray-50 px-6 py-4 space-y-3">
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="审批备注（可选）"
              rows={2}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-100 resize-none"
            />
            <div className="flex gap-3">
              <button
                onClick={() => handleAction('reject')}
                disabled={actionLoading}
                className="flex-1 rounded-lg border border-red-300 bg-white px-4 py-2.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50"
              >
                拒绝
              </button>
              <button
                onClick={() => handleAction('approve')}
                disabled={actionLoading}
                className="flex-1 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
              >
                {actionLoading ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                    处理中...
                  </span>
                ) : '通过'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Section Card sub-component ──────────────────────────────────────────

function SectionCard({ section, selection, setSelection, disabled }: {
  section: CardSection
  selection: Record<string, SectionState>
  setSelection: React.Dispatch<React.SetStateAction<Record<string, SectionState>>>
  disabled: boolean
}) {
  const handleActionClick = (sectionId: string, fieldId: string, actionKey: string) => {
    if (disabled) return
    setSelection((prev) => {
      const sec = { ...(prev[sectionId] ?? {}) }
      if (sec[fieldId]?.action === actionKey) {
        delete sec[fieldId]
      } else {
        sec[fieldId] = { action: actionKey, value: sec[fieldId]?.value }
      }
      return { ...prev, [sectionId]: sec }
    })
  }

  const handleCustomChange = (sectionId: string, fieldId: string, value: string) => {
    if (disabled) return
    setSelection((prev) => {
      const sec = { ...(prev[sectionId] ?? {}) }
      sec[fieldId] = { action: 'customize', value }
      return { ...prev, [sectionId]: sec }
    })
  }

  const styleMap: Record<string, { border: string; bg: string; icon: string }> = {
    default: { border: '#3b82f6', bg: '#eff6ff', icon: '📋' },
    success: { border: '#16a34a', bg: '#f0fdf4', icon: '✅' },
    info: { border: '#06b6d4', bg: '#ecfeff', icon: 'ℹ️' },
    warning: { border: '#f59e0b', bg: '#fffbeb', icon: '⚠️' },
    danger: { border: '#e5484d', bg: '#fef2f2', icon: '🚨' },
  }
  const style = styleMap[section.style ?? 'default']

  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden border-l-4" style={{ borderLeftColor: style.border }}>
      <div className="px-4 py-3 border-b border-slate-100" style={{ background: style.bg }}>
        <div className="flex items-center gap-1.5 text-sm font-semibold text-slate-800">
          <span>{section.icon ?? style.icon}</span>
          {section.title}
        </div>
        {section.description && (
          <div className="mt-1 text-xs text-slate-500">{section.description}</div>
        )}
      </div>
      <div className="px-4 py-2">
        {section.fields.map((field, i) => {
          const isInteractive = (field.actions?.length ?? 0) > 0 || field.customizable
          const sel = selection[section.id]?.[field.id]
          const isCustomActive = sel?.action === 'customize'
          return (
            <div key={field.id} className={`py-2.5 ${i < section.fields.length - 1 ? 'border-b border-slate-50' : ''}`}>
              <div className="text-sm font-medium text-slate-700 mb-2">{field.label}</div>
              {!isInteractive && (field.expected || field.actual) && (
                <div className="space-y-1">
                  {field.expected && (
                    <div className="rounded-md bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
                      {field.expectedLabel && <span className="text-xs text-slate-400 mr-1">{field.expectedLabel}:</span>}
                      {field.expected}
                    </div>
                  )}
                  {field.actual && (
                    <div className="rounded-md bg-slate-50 px-3 py-1.5 text-sm text-slate-600">
                      {field.actualLabel && <span className="text-xs text-slate-400 mr-1">{field.actualLabel}:</span>}
                      {field.actual}
                    </div>
                  )}
                </div>
              )}
              {isInteractive && field.expected && !field.actual && (
                <div className="mb-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600 leading-relaxed whitespace-pre-wrap break-words">
                  {field.expectedLabel && <div className="text-xs text-slate-400 mb-1">{field.expectedLabel}</div>}
                  {field.expected}
                </div>
              )}
              {isInteractive && field.actions?.map((action) => {
                const isActive = sel?.action === action.key
                return (
                  <button
                    key={action.key}
                    onClick={() => handleActionClick(section.id, field.id, action.key)}
                    disabled={disabled}
                    className={`mr-2 mb-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      isActive
                        ? action.type === 'primary'
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : action.type === 'danger'
                            ? 'border-red-500 bg-red-50 text-red-700'
                            : 'border-slate-400 bg-slate-50 text-slate-700'
                        : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                    } disabled:opacity-50 disabled:cursor-not-allowed`}
                  >
                    {action.label}
                  </button>
                )
              })}
              {isCustomActive && (
                <textarea
                  value={sel?.value ?? ''}
                  onChange={(e) => handleCustomChange(section.id, field.id, e.target.value)}
                  placeholder={field.placeholder ?? '请输入自定义内容'}
                  rows={2}
                  disabled={disabled}
                  className="mt-2 w-full rounded-md border border-blue-300 px-3 py-2 text-sm text-slate-700 outline-none resize-none focus:ring-2 focus:ring-blue-100"
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
