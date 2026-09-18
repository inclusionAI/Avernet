import { useState, useEffect, useCallback } from 'react'
import { approvalDisplay, type ApprovalDisplay } from '../../shared/approval-display'
import { formatDuration } from '../utils/time'
import { useParams, useSearchParams } from 'react-router-dom'

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

type FieldSelection = {
  action: string
  value?: string
}

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
  approvalPolicy: 'any' | 'all' | 'majority'
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
  empId?: string
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

type StatusResult = {
  id: number
  flowId: string
  nodeId: string
  status: ApprovalStatus
  approvalPolicy: 'any' | 'all' | 'majority'
  approvedBy: string[]
  rejectedBy: string[]
  resolvedAt: number | null
}

type DingTalkAuthResult = {
  ok: boolean
  userId?: string
  error?: string
}

// ── DingTalk JSAPI ─────────────────────────────────────────────────────

declare global {
  interface Window {
    dd?: {
      ready: (callback: () => void) => void
      runtime: {
        permission: {
          requestAuthCode: (params: {
            corpId: string
            onSuccess: (result: { code: string }) => void
            onFail: (err: unknown) => void
          }) => void
        }
      }
    }
  }
}

function getDingTalkUserId(corpId: string): Promise<DingTalkAuthResult> {
  return new Promise((resolve) => {
    if (!window.dd?.ready) {
      resolve({ ok: false, error: '非钉钉环境' })
      return
    }

    window.dd.ready(() => {
      if (!window.dd?.runtime?.permission?.requestAuthCode) {
        resolve({ ok: false, error: '钉钉 JSAPI 不可用' })
        return
      }

      window.dd.runtime.permission.requestAuthCode({
        corpId,
        onSuccess: async (result: { code: string }) => {
          try {
            const res = await fetch('/api/approval/auth/dingtalk', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ authCode: result.code }),
            })
            const data: DingTalkAuthResult = await res.json()
            resolve(data)
          } catch {
            resolve({ ok: false, error: '钉钉认证请求失败' })
          }
        },
        onFail: (err: unknown) => {
          const msg = err instanceof Error ? err.message : '获取授权码失败'
          resolve({ ok: false, error: msg })
        },
      })
    })

    setTimeout(() => {
      resolve({ ok: false, error: '钉钉环境检测超时' })
    }, 3000)
  })
}

// ── API helpers ────────────────────────────────────────────────────────

async function resolveApproval(
  id: number,
  empId: string,
  action: 'approve' | 'reject',
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

async function pollStatus(id: number): Promise<StatusResult> {
  const res = await fetch(`/api/approval/${id}/status`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ── Format helper ──────────────────────────────────────────────────────

function formatTime(ts: number | null): string {
  if (!ts) return '—'
  const ms = ts > 1e12 ? ts : ts * 1000
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ── Design tokens ──────────────────────────────────────────────────────

const COLOR = {
  primary: '#2f6fed',
  primaryLight: '#eef3fe',
  primaryBorder: '#c5d8fd',
  success: '#16a34a',
  successLight: '#f0fdf4',
  successBorder: '#bbf7d0',
  danger: '#e5484d',
  dangerLight: '#fef2f2',
  dangerBorder: '#fecaca',
  warning: '#f59e0b',
  warningLight: '#fffbeb',
  text: '#1a1a1a',
  textSecondary: '#6b7280',
  textTertiary: '#9ca3af',
  bg: '#f8f9fb',
  card: '#ffffff',
  border: '#e5e7eb',
  borderLight: '#f3f4f6',
}

const STATUS_STYLE: Record<ApprovalStatus, { bg: string; border: string; text: string; icon: string; gradient: string }> = {
  pending: { bg: COLOR.warningLight, border: '#fde68a', text: '#92400e', icon: '⏳', gradient: 'linear-gradient(135deg, #fde68a 0%, #fef3c7 100%)' },
  approved: { bg: COLOR.successLight, border: COLOR.successBorder, text: COLOR.success, icon: '✓', gradient: 'linear-gradient(135deg, #d1fae5 0%, #ecfdf5 100%)' },
  rejected: { bg: COLOR.dangerLight, border: COLOR.dangerBorder, text: COLOR.danger, icon: '✕', gradient: 'linear-gradient(135deg, #fee2e2 0%, #fef2f2 100%)' },
}

const SECTION_STYLE: Record<string, { border: string; bg: string; icon: string }> = {
  default: { border: COLOR.primary, bg: COLOR.primaryLight, icon: '📋' },
  success: { border: COLOR.success, bg: COLOR.successLight, icon: '✅' },
  info: { border: '#06b6d4', bg: '#ecfeff', icon: 'ℹ️' },
  warning: { border: COLOR.warning, bg: COLOR.warningLight, icon: '⚠️' },
  danger: { border: COLOR.danger, bg: COLOR.dangerLight, icon: '🚨' },
}

// ── Section Card Component ───────────────────────────────────────────────

type SectionState = Record<string, FieldSelection>

type SectionCardProps = {
  sections: CardSection[]
  selection: Record<string, SectionState>
  setSelection: React.Dispatch<React.SetStateAction<Record<string, SectionState>>>
  disabled: boolean
}

function SectionCard({ sections, selection, setSelection, disabled }: SectionCardProps) {
  const handleActionClick = (sectionId: string, fieldId: string, actionKey: string) => {
    if (disabled) return
    setSelection((prev) => {
      const sec = { ...(prev[sectionId] ?? {}) }
      // Toggle: clicking the same action deselects it
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {sections.map((section) => {
        const style = SECTION_STYLE[section.style ?? 'default']
        return (
          <div
            key={section.id}
            style={{
              background: COLOR.card,
              borderRadius: 12,
              border: `1px solid ${COLOR.border}`,
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              overflow: 'hidden',
              borderLeft: `4px solid ${style.border}`,
            }}
          >
            {/* Section header */}
            <div style={{ padding: '12px 16px', borderBottom: `1px solid ${COLOR.borderLight}`, background: style.bg }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: COLOR.text, display: 'flex', alignItems: 'center', gap: 6, lineHeight: 1.4 }}>
                <span style={{ fontSize: 16 }}>{section.icon ?? style.icon}</span>
                {section.title}
              </div>
              {section.description && (
                <div style={{ fontSize: 12, color: COLOR.textSecondary, marginTop: 4, lineHeight: 1.5 }}>
                  {section.description}
                </div>
              )}
            </div>

            {/* Fields */}
            <div style={{ padding: '2px 16px' }}>
              {section.fields.map((field, fieldIdx) => {
                const fieldSel = selection[section.id]?.[field.id]
                const isCustomActive = fieldSel?.action === 'customize'
                const isLastField = fieldIdx === section.fields.length - 1

                return (
                  <div
                    key={field.id}
                    style={{
                      padding: '10px 0',
                      borderBottom: isLastField ? 'none' : `1px solid ${COLOR.borderLight}`,
                    }}
                  >
                    <div style={{ fontSize: 13, fontWeight: 500, color: COLOR.text, marginBottom: 8 }}>
                      {field.label}
                    </div>

                    {/* Adaptive value rendering: compare / reference / display */}
                    {(() => {
                      const hasActions = field.actions && field.actions.length > 0
                      const hasCustomizable = field.customizable === true
                      const isInteractive = hasActions || hasCustomizable
                      const hasExpected = field.expected !== undefined
                      const hasActual = field.actual !== undefined

                      // ── Mode 1: DISPLAY (read-only) ──
                      if (!isInteractive && (hasExpected || hasActual)) {
                        return (
                          <div style={{
                            padding: '8px 10px',
                            background: COLOR.borderLight,
                            borderRadius: 8,
                            marginBottom: 0,
                            fontSize: 13,
                            color: '#374151',
                            lineHeight: 1.6,
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}>
                            {hasExpected && (
                              <div style={{ marginBottom: hasActual ? 6 : 0 }}>
                                {field.expectedLabel && field.actual && (
                                  <div style={{ fontSize: 11, color: COLOR.textTertiary, marginBottom: 2 }}>{field.expectedLabel}</div>
                                )}
                                <div>{field.expected || '—'}</div>
                              </div>
                            )}
                            {hasActual && (
                              <div>
                                {field.actualLabel && field.expected && (
                                  <div style={{ fontSize: 11, color: COLOR.textTertiary, marginBottom: 2 }}>{field.actualLabel}</div>
                                )}
                                <div>{field.actual || '—'}</div>
                              </div>
                            )}
                          </div>
                        )
                      }

                      // ── Mode 2: COMPARE (dual-column) ──
                      if (isInteractive && hasExpected && hasActual) {
                        return (
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 1fr',
                            gap: 10,
                            marginBottom: 10,
                          }}>
                            {hasExpected && (
                              <div style={{ background: COLOR.successLight, borderRadius: 8, padding: '8px 10px' }}>
                                {field.expectedLabel && (
                                  <div style={{ fontSize: 11, color: COLOR.success, marginBottom: 2, fontWeight: 500 }}>{field.expectedLabel}</div>
                                )}
                                <div style={{ fontSize: 13, color: '#374151', lineHeight: 1.5 }}>{field.expected || '—'}</div>
                              </div>
                            )}
                            {hasActual && (
                              <div style={{ background: '#eff6ff', borderRadius: 8, padding: '8px 10px' }}>
                                {field.actualLabel && (
                                  <div style={{ fontSize: 11, color: COLOR.primary, marginBottom: 2, fontWeight: 500 }}>{field.actualLabel}</div>
                                )}
                                <div style={{ fontSize: 13, color: '#374151', lineHeight: 1.5 }}>{field.actual || '—'}</div>
                              </div>
                            )}
                          </div>
                        )
                      }

                      return null
                    })()}

                    {/* Action buttons */}
                    {field.actions && field.actions.length > 0 && (
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {field.actions.map((action) => {
                          const isSelected = fieldSel?.action === action.key
                          const actionColorMap: Record<string, { bg: string; text: string; border: string }> = {
                            primary: { bg: COLOR.primary, text: '#fff', border: COLOR.primary },
                            danger: { bg: '#fff', text: COLOR.danger, border: COLOR.danger },
                            default: { bg: COLOR.borderLight, text: '#4b5563', border: '#d1d5db' },
                          }
                          const c = actionColorMap[action.type ?? 'default'] ?? actionColorMap.default
                          return (
                            <button
                              key={action.key}
                              onClick={() => handleActionClick(section.id, field.id, action.key)}
                              disabled={disabled}
                              style={{
                                padding: '6px 14px',
                                borderRadius: 8,
                                border: `1px solid ${isSelected ? c.border : '#d9d9d9'}`,
                                background: isSelected ? c.bg : '#fff',
                                color: isSelected ? c.text : '#4b5563',
                                fontSize: 13,
                                cursor: disabled ? 'not-allowed' : 'pointer',
                                opacity: disabled ? 0.5 : 1,
                                fontWeight: isSelected ? 500 : 400,
                                transition: 'all 0.15s ease',
                              }}
                            >
                              {isSelected && '✓ '}
                              {action.label}
                            </button>
                          )
                        })}
                      </div>
                    )}

                    {/* Custom input */}
                    {(isCustomActive || (!field.actions && field.customizable)) && (
                      <textarea
                        value={fieldSel?.value ?? ''}
                        onChange={(e) => handleCustomChange(section.id, field.id, e.target.value)}
                        placeholder={field.placeholder ?? '请输入自定义内容'}
                        rows={2}
                        disabled={disabled || (!isCustomActive && !!field.actions)}
                        style={{
                          width: '100%',
                          marginTop: 8,
                          border: `1px solid ${isCustomActive ? COLOR.primary : COLOR.border}`,
                          borderRadius: 8,
                          padding: '8px 10px',
                          fontSize: 13,
                          color: COLOR.text,
                          resize: 'none',
                          outline: 'none',
                          boxSizing: 'border-box',
                          background: isCustomActive ? '#fff' : COLOR.borderLight,
                          cursor: disabled || (!isCustomActive && !!field.actions) ? 'not-allowed' : 'text',
                          transition: 'border-color 0.15s ease',
                        }}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Validation helper ──────────────────────────────────────────────────

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
          if (!trimmed) {
            derivedValue = actionDef?.autoFill || field.expected || null
          }
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
    if (Object.keys(sectionData).length > 0) {
      payload[section.id] = sectionData
    }
  }
  return payload
}

// ── Avatar helper ──────────────────────────────────────────────────────

function getAvatarColor(name: string): { bg: string; text: string } {
  const colors = [
    { bg: '#e0e7ff', text: '#4338ca' },
    { bg: '#fce7f3', text: '#9d174d' },
    { bg: '#d1fae5', text: '#065f46' },
    { bg: '#fef3c7', text: '#92400e' },
    { bg: '#e0f2fe', text: '#075985' },
    { bg: '#f3e8ff', text: '#6b21a8' },
  ]
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  return colors[Math.abs(hash) % colors.length]
}

// ── Approval page ──────────────────────────────────────────────────────

export default function Approval() {
  const { id: idStr } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()

  const [data, setData] = useState<ApprovalData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [comment, setComment] = useState('')
  const [result, setResult] = useState<ResolveResult | null>(null)
  const [sectionSelection, setSectionSelection] = useState<Record<string, SectionState>>({})
  const [validationError, setValidationError] = useState<string | null>(null)

  const [empId, setEmpId] = useState<string>('')
  const [identitySource, setIdentitySource] = useState<'dingtalk' | 'url' | 'unknown'>('unknown')

  const approvalId = idStr ? parseInt(idStr, 10) : NaN

  const isSectionMode = !!data?.sections && data.sections.length > 0

  // ── Identity resolution ──────────────────────────────────────────────
  useEffect(() => {
    if (Number.isNaN(approvalId)) {
      setError('无效的审批 ID')
      setLoading(false)
      return
    }

    let cancelled = false

    async function resolveIdentity() {
      const corpId = searchParams.get('corpId') ?? ''
      if (corpId) {
        const dtResult = await getDingTalkUserId(corpId)
        if (!cancelled && dtResult.ok && dtResult.userId) {
          setEmpId(dtResult.userId)
          setIdentitySource('dingtalk')
          return
        }
      }

      const urlEmpId = searchParams.get('empId') ?? ''
      if (urlEmpId) {
        setEmpId(urlEmpId)
        setIdentitySource('url')
        return
      }

      if (!cancelled) {
        setIdentitySource('unknown')
      }
    }

    resolveIdentity()
    return () => { cancelled = true }
  }, [approvalId, searchParams])

  // ── Load approval data ───────────────────────────────────────────────
  useEffect(() => {
    if (Number.isNaN(approvalId)) return

    let cancelled = false
    const url = empId
      ? `/api/approval/${approvalId}?empId=${encodeURIComponent(empId)}`
      : `/api/approval/${approvalId}`

    fetch(url)
      .then((res) => {
        if (!res.ok) {
          return res.json().catch(() => ({})).then((body) => {
            throw new Error(body.message || `HTTP ${res.status}`)
          })
        }
        return res.json()
      })
      .then((d: ApprovalData) => {
        if (!cancelled) {
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
                  if (fd && typeof fd.action === 'string') {
                    initial[section.id][field.id] = {
                      action: fd.action as string,
                      value: typeof fd.value === 'string' ? fd.value : undefined,
                    }
                  }
                }
              }
            }
            if (Object.keys(initial).length > 0) {
              setSectionSelection(initial)
            }
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载审批数据失败')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [approvalId, empId])

  // Poll status every 3s when pending
  useEffect(() => {
    if (Number.isNaN(approvalId) || !data || data.status !== 'pending') return
    const interval = setInterval(() => {
      pollStatus(approvalId)
        .then((s) => {
          setData((prev) =>
            prev
              ? { ...prev, status: s.status, approvedBy: s.approvedBy, rejectedBy: s.rejectedBy, resolvedAt: s.resolvedAt }
              : prev,
          )
        })
        .catch(() => { /* ignore */ })
    }, 3000)
    return () => clearInterval(interval)
  }, [approvalId, data?.status])

  const handleAction = useCallback(
    async (action: 'approve' | 'reject') => {
      if (Number.isNaN(approvalId)) return

      if (isSectionMode && data?.sections) {
        const err = validateSections(data.sections, sectionSelection)
        if (err) {
          setValidationError(err)
          return
        }
        setValidationError(null)
      }

      if (!empId) {
        const corpId = searchParams.get('corpId') ?? ''
        if (corpId && window.dd?.ready) {
          setActionLoading(true)
          const dtResult = await getDingTalkUserId(corpId)
          if (dtResult.ok && dtResult.userId) {
            setEmpId(dtResult.userId)
            setIdentitySource('dingtalk')
            try {
              const detail = isSectionMode && data?.sections
                ? buildDetailPayload(data.sections, sectionSelection)
                : undefined
              const res = await resolveApproval(approvalId, dtResult.userId, action, comment || undefined, detail)
              setResult(res)
              if (res.ok) {
                const freshUrl = `/api/approval/${approvalId}?empId=${encodeURIComponent(dtResult.userId)}`
                const fresh = await (await fetch(freshUrl)).json()
                setData(fresh)
              }
            } catch (err) {
              setResult({ error: err instanceof Error ? err.message : '操作失败' })
            } finally {
              setActionLoading(false)
            }
            return
          }
        }
        setResult({ error: '无法获取用户身份，请在钉钉中打开此页面' })
        return
      }

      setActionLoading(true)
      try {
        const detail = isSectionMode && data?.sections
          ? buildDetailPayload(data.sections, sectionSelection)
          : undefined
        const res = await resolveApproval(approvalId, empId, action, comment || undefined, detail)
        setResult(res)
        if (res.ok) {
          const freshUrl = `/api/approval/${approvalId}?empId=${encodeURIComponent(empId)}`
          const fresh = await (await fetch(freshUrl)).json()
          setData(fresh)
        }
      } catch (err) {
        setResult({ error: err instanceof Error ? err.message : '操作失败' })
      } finally {
        setActionLoading(false)
      }
    },
    [approvalId, empId, comment, searchParams, isSectionMode, data?.sections, sectionSelection],
  )

  // ── Loading ──────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', background: COLOR.bg }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 36, height: 36, border: `3px solid ${COLOR.primary}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 16px' }} />
          <p style={{ color: COLOR.textSecondary, fontSize: 14, margin: 0 }}>加载审批信息...</p>
          <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
        </div>
      </div>
    )
  }

  // ── Error ────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', background: COLOR.bg, padding: 20 }}>
        <div style={{ background: COLOR.card, borderRadius: 16, padding: 32, textAlign: 'center', maxWidth: 360, width: '100%', border: `1px solid ${COLOR.border}`, boxShadow: '0 4px 24px rgba(0,0,0,0.06)' }}>
          <div style={{ width: 56, height: 56, margin: '0 auto 16px', borderRadius: '50%', background: COLOR.dangerLight, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24 }}>
            🔒
          </div>
          <h2 style={{ fontSize: 17, fontWeight: 600, color: COLOR.text, marginBottom: 8, margin: '0 0 8px' }}>无法访问审批</h2>
          <p style={{ fontSize: 13, color: COLOR.textSecondary, margin: 0 }}>{error}</p>
          <p style={{ fontSize: 12, color: COLOR.textTertiary, marginTop: 12 }}>该链接可能已过期或无效</p>
        </div>
      </div>
    )
  }

  if (!data) return null

  const isResolved = data.status === 'approved' || data.status === 'rejected'
  const canAct = data.status === 'pending'
  const stStyle = STATUS_STYLE[data.status]

  const approverList = data.approverIds.map((id, i) => ({
    id,
    name: data.approverNames[i] || id,
    approved: data.approvedBy.includes(id),
    rejected: data.rejectedBy.includes(id),
    isCurrent: id === empId,
  }))

  const policyLabel: Record<string, string> = {
    any: '任一审批人通过即可',
    all: '需全部审批人通过',
    majority: '多数审批人通过即可',
  }

  const copy = approvalDisplay(data.approvalType, data.display)
  const statusCopy = data.status === 'pending' ? copy.pendingText : data.status === 'approved' ? copy.approvedText : copy.rejectedText
  const elapsedEnd = data.status === 'pending' ? Math.floor(Date.now() / 1000) : data.resolvedAt
  const elapsedMs = data.createdAt > 0 && elapsedEnd != null && elapsedEnd >= data.createdAt
    ? (elapsedEnd - data.createdAt) * 1000
    : null

  function renderCardFieldValue(field: CardField): React.ReactNode {
    if (field.label === '申请单' && field.value) {
      return (
        <a
          href={field.value}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: COLOR.primary, fontSize: 14, textDecoration: 'none', fontWeight: 500 }}
          onClick={(e) => e.stopPropagation()}
        >
          点击打开申请单 →
        </a>
      )
    }
    if (field.value.length > 400) {
      return (
        <details>
          <summary style={{ cursor: 'pointer', color: COLOR.primary, fontWeight: 400, fontSize: 13 }}>
            {field.value.slice(0, 160)}… <span style={{ color: COLOR.primary }}>展开完整内容</span>
          </summary>
          <div style={{ marginTop: 12, maxHeight: 360, overflow: 'auto', fontWeight: 400, fontSize: 13, lineHeight: 1.6, color: '#374151' }}>
            {field.value}
          </div>
        </details>
      )
    }
    return field.value || '—'
  }

  return (
    <div style={{ minHeight: '100vh', background: COLOR.bg, padding: '16px 12px', color: COLOR.text }}>
      <div style={{ maxWidth: 580, margin: '0 auto', overflowWrap: 'anywhere' }}>
        {/* ── Title Card ─────────────────────────────────────────────── */}
        <div style={{
          background: COLOR.card,
          borderRadius: 16,
          border: `1px solid ${COLOR.border}`,
          boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.03)',
          marginBottom: 12,
          overflow: 'hidden',
        }}>
          {/* Header bar with status-aware gradient */}
          <div style={{
            background: stStyle.gradient,
            borderTop: `3px solid ${stStyle.border}`,
            borderBottom: `1px solid ${stStyle.border}`,
            padding: '20px 20px 16px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <span style={{
                fontSize: 12,
                fontWeight: 500,
                color: COLOR.textSecondary,
                textTransform: 'uppercase',
                letterSpacing: 0.5,
              }}>{copy.subtitle}</span>
              <span style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                background: COLOR.card,
                color: stStyle.text,
                border: `1px solid ${stStyle.border}`,
                borderRadius: 20,
                padding: '3px 12px',
                fontSize: 12,
                fontWeight: 500,
              }}>
                {stStyle.icon} {statusCopy}
              </span>
            </div>
            <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0, lineHeight: 1.4, color: COLOR.text }}>
              {copy.title ?? (data.message || data.workflowTitle || '审批请求')}
            </h1>
          </div>

          {/* Section mode — interactive section cards */}
          {isSectionMode && data.sections && (
            <div style={{ padding: '16px' }}>
              <SectionCard
                sections={data.sections}
                selection={sectionSelection}
                setSelection={setSectionSelection}
                disabled={!canAct}
              />
            </div>
          )}

          {/* Traditional mode — stacked card fields */}
          <div style={{ padding: '10px 16px', background: COLOR.borderLight, borderBottom: `1px solid ${COLOR.borderLight}`, fontSize: 12, color: COLOR.textSecondary, display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ opacity: 0.6 }}>{data.status === 'pending' ? '⏱' : '✓'}</span>
            {data.status === 'pending' ? '等待确认' : '确认耗时'} · {elapsedMs === null ? '—' : elapsedMs === 0 ? '0s' : formatDuration(elapsedMs)}
          </div>
          {!isSectionMode && data.cardFields.length > 0 && (
            <div style={{ padding: '8px 16px' }}>
              {data.cardFields.map((field, i) => (
                <div key={i} style={{
                  padding: '12px 0',
                  borderBottom: i < data.cardFields.length - 1 ? `1px solid ${COLOR.borderLight}` : 'none',
                }}>
                  <div style={{ fontSize: 12, color: COLOR.textTertiary, marginBottom: 4, fontWeight: 500 }}>{field.label}</div>
                  <div style={{ fontSize: 14, color: COLOR.text, fontWeight: 400, lineHeight: 1.6, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                    {renderCardFieldValue(field)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Detail Card ────────────────────────────────────────────── */}
        {!['SUPPLEMENT_COMPLETE', 'HUMAN_CONFIRM'].includes(data.approvalType ?? '') && (
          <div style={{
            background: COLOR.card,
            borderRadius: 16,
            border: `1px solid ${COLOR.border}`,
            boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.03)',
            marginBottom: 12,
            overflow: 'hidden',
          }}>
            {/* Approver section */}
            <div style={{ padding: '16px', borderBottom: `1px solid ${COLOR.borderLight}` }}>
              <div style={{ fontSize: 12, color: COLOR.textTertiary, marginBottom: 10, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 0.5 }}>审批人</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {approverList.map((a) => {
                  const avatar = getAvatarColor(a.name)
                  return (
                    <div key={a.id} style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      padding: '5px 12px 5px 5px',
                      borderRadius: 20,
                      fontSize: 13,
                      background: a.approved
                        ? COLOR.successLight
                        : a.rejected
                          ? COLOR.dangerLight
                          : COLOR.borderLight,
                      color: a.approved
                        ? COLOR.success
                        : a.rejected
                          ? COLOR.danger
                          : '#4b5563',
                      border: a.isCurrent ? `2px solid ${COLOR.primary}` : a.approved ? `1px solid ${COLOR.successBorder}` : a.rejected ? `1px solid ${COLOR.dangerBorder}` : '1px solid transparent',
                      transition: 'all 0.2s ease',
                    }}>
                      <span style={{
                        width: 24, height: 24, borderRadius: '50%',
                        background: avatar.bg, color: avatar.text,
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 11, fontWeight: 600, flexShrink: 0,
                      }}>
                        {a.name.charAt(0).toUpperCase()}
                      </span>
                      {a.approved && '✓ '}
                      {a.rejected && '✗ '}
                      {a.name}
                      {a.isCurrent && <span style={{ fontSize: 11, color: COLOR.primary, fontWeight: 500 }}>(我)</span>}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Policy */}
            <div style={{ padding: '12px 16px', borderBottom: `1px solid ${COLOR.borderLight}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, color: COLOR.textSecondary }}>审批策略</span>
              <span style={{ fontSize: 13, color: COLOR.text, fontWeight: 500 }}>{policyLabel[data.approvalPolicy] ?? data.approvalPolicy}</span>
            </div>

            {/* Workflow info */}
            <div style={{ padding: '12px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, color: COLOR.textSecondary }}>发起时间</span>
              <span style={{ fontSize: 13, color: COLOR.text }}>{formatTime(data.createdAt)}</span>
            </div>

            {data.resolvedAt && (
              <div style={{ padding: '12px 16px', borderTop: `1px solid ${COLOR.borderLight}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: COLOR.textSecondary }}>
                  {data.status === 'approved' ? '通过时间' : '拒绝时间'}
                </span>
                <span style={{ fontSize: 13, color: COLOR.text }}>{formatTime(data.resolvedAt)}</span>
              </div>
            )}
          </div>
        )}

        {/* ── Action Card ────────────────────────────────────────────── */}
        {canAct && (
          <div style={{
            background: COLOR.card,
            borderRadius: 16,
            border: `1px solid ${COLOR.border}`,
            boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.03)',
            marginBottom: 12,
            padding: 16,
          }}>
            {/* Validation error */}
            {validationError && isSectionMode && (
              <div style={{
                background: COLOR.dangerLight,
                border: `1px solid ${COLOR.dangerBorder}`,
                borderRadius: 10,
                padding: '10px 14px',
                marginBottom: 12,
                fontSize: 13,
                color: COLOR.danger,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}>
                <span>⚠️</span> {validationError}
              </div>
            )}

            {/* Comment */}
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={copy.notePlaceholder}
              aria-label={copy.notePlaceholder}
              rows={2}
              style={{
                width: '100%',
                border: `1px solid ${COLOR.border}`,
                borderRadius: 10,
                padding: '10px 14px',
                fontSize: 14,
                color: COLOR.text,
                resize: 'none',
                outline: 'none',
                marginBottom: 12,
                boxSizing: 'border-box',
                background: COLOR.card,
                transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
              }}
              onFocus={(e) => { e.target.style.borderColor = COLOR.primary; e.target.style.boxShadow = `0 0 0 3px ${COLOR.primaryLight}` }}
              onBlur={(e) => { e.target.style.borderColor = COLOR.border; e.target.style.boxShadow = 'none' }}
            />

            {/* Buttons */}
            <div style={{ display: 'flex', gap: 12 }}>
              <button
                onClick={() => handleAction('reject')}
                disabled={actionLoading}
                style={{
                  flex: 1,
                  minHeight: 46, padding: '10px 16px',
                  borderRadius: 10,
                  border: `1px solid ${COLOR.danger}`,
                  background: COLOR.card,
                  color: COLOR.danger,
                  fontSize: 15,
                  fontWeight: 500,
                  cursor: actionLoading ? 'not-allowed' : 'pointer',
                  opacity: actionLoading ? 0.5 : 1,
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={(e) => { if (!actionLoading) { e.currentTarget.style.background = COLOR.dangerLight; e.currentTarget.style.borderColor = COLOR.danger } }}
                onMouseLeave={(e) => { if (!actionLoading) { e.currentTarget.style.background = COLOR.card } }}
              >
                {copy.rejectLabel}
              </button>
              <button
                onClick={() => handleAction('approve')}
                disabled={actionLoading}
                style={{
                  flex: 1,
                  minHeight: 46, padding: '10px 16px',
                  borderRadius: 10,
                  border: 'none',
                  background: COLOR.primary,
                  color: '#fff',
                  fontSize: 15,
                  fontWeight: 500,
                  cursor: actionLoading ? 'not-allowed' : 'pointer',
                  opacity: actionLoading ? 0.5 : 1,
                  transition: 'all 0.15s ease',
                  boxShadow: `0 2px 8px ${COLOR.primary}33`,
                }}
                onMouseEnter={(e) => { if (!actionLoading) { e.currentTarget.style.boxShadow = `0 4px 12px ${COLOR.primary}44` } }}
                onMouseLeave={(e) => { if (!actionLoading) { e.currentTarget.style.boxShadow = `0 2px 8px ${COLOR.primary}33` } }}
              >
                {actionLoading ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.4)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.6s linear infinite' }} />
                    处理中...
                  </span>
                ) : copy.confirmLabel}
              </button>
            </div>
            <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
          </div>
        )}

        {/* ── Resolved status ────────────────────────────────────────── */}
        {isResolved && (
          <div style={{
            background: COLOR.card,
            borderRadius: 16,
            border: `1px solid ${stStyle.border}`,
            boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.03)',
            marginBottom: 12,
            padding: '24px 16px',
            textAlign: 'center',
          }}>
            <div style={{
              width: 48, height: 48, margin: '0 auto 12px',
              borderRadius: '50%',
              background: data.status === 'approved' ? COLOR.successLight : COLOR.dangerLight,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 24, fontWeight: 700,
              color: data.status === 'approved' ? COLOR.success : COLOR.danger,
            }}>
              {data.status === 'approved' ? '✓' : '✕'}
            </div>
            <div style={{ fontSize: 16, fontWeight: 600, color: data.status === 'approved' ? COLOR.success : COLOR.danger }}>
              {data.status === 'approved' ? copy.approvedText : copy.rejectedText}
            </div>
            {isSectionMode && data.detail && (
              <div style={{ marginTop: 16, textAlign: 'left' }}>
                {data.sections?.map((section) => {
                  const secDetail = data.detail?.[section.id] as Record<string, { actionLabel?: string; value?: string | null }> | undefined
                  if (!secDetail) return null
                  return (
                    <div key={section.id} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: COLOR.text, marginBottom: 4 }}>{section.title}</div>
                      {section.fields.map((field) => {
                        const fd = secDetail[field.id]
                        if (!fd) return null
                        return (
                          <div key={field.id} style={{ fontSize: 12, color: COLOR.textSecondary, paddingLeft: 12, marginTop: 2 }}>
                            {field.label}: <span style={{ color: COLOR.text, fontWeight: 500 }}>{fd.actionLabel}</span>
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

        {/* ── Result message ─────────────────────────────────────────── */}
        {result && (
          <div style={{
            borderRadius: 12,
            padding: '12px 16px',
            fontSize: 13,
            background: result.error ? COLOR.dangerLight : COLOR.successLight,
            color: result.error ? COLOR.danger : COLOR.success,
            border: `1px solid ${result.error ? COLOR.dangerBorder : COLOR.successBorder}`,
            marginBottom: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}>
            <span>{result.error ? '⚠️' : '✓'}</span>
            {result.error
              ? `操作失败: ${result.error}`
              : result.status === 'approved'
                ? copy.approvedText
                : result.status === 'rejected'
                  ? copy.rejectedText
                  : '操作成功，等待其他审批人...'}
          </div>
        )}

        {/* ── Footer ─────────────────────────────────────────────────── */}
        <div style={{ textAlign: 'center', padding: '8px 0 16px', fontSize: 11, color: COLOR.textTertiary }}>
          {copy.footer}
        </div>
      </div>
    </div>
  )
}
