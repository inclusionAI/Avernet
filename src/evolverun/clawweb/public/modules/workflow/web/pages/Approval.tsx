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
  if (!ts) return ''
  const ms = typeof ts === 'number'
    ? (ts > 1e12 ? ts : ts * 1000)
    : new Date(ts).getTime()
  return new Date(ms).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ── Section Card Component ───────────────────────────────────────────────

type SectionState = Record<string, FieldSelection>

type SectionCardProps = {
  sections: CardSection[]
  selection: Record<string, SectionState>
  setSelection: React.Dispatch<React.SetStateAction<Record<string, SectionState>>>
  disabled: boolean
}

const sectionStyleMap: Record<string, { borderLeft: string; icon: string }> = {
  default: { borderLeft: '4px solid #1677ff', icon: '📋' },
  success: { borderLeft: '4px solid #52c41a', icon: '🎉' },
  info: { borderLeft: '4px solid #13c2c2', icon: 'ℹ️' },
  warning: { borderLeft: '4px solid #fa8c16', icon: '⚠️' },
  danger: { borderLeft: '4px solid #ff4d4f', icon: '🚨' },
}

function SectionCard({ sections, selection, setSelection, disabled }: SectionCardProps) {
  const handleActionClick = (sectionId: string, fieldId: string, actionKey: string) => {
    if (disabled) return
    setSelection((prev) => {
      const next = { ...prev }
      if (!next[sectionId]) next[sectionId] = {}
      // If clicking the same action, deselect
      if (next[sectionId][fieldId]?.action === actionKey) {
        const sNext = { ...next[sectionId] }
        delete sNext[fieldId]
        next[sectionId] = sNext
      } else {
        next[sectionId] = {
          ...next[sectionId],
          [fieldId]: {
            action: actionKey,
            value: actionKey === 'customize'
              ? (prev[sectionId]?.[fieldId]?.value ?? '')
              : undefined,
          },
        }
      }
      return next
    })
  }

  const handleCustomChange = (sectionId: string, fieldId: string, value: string) => {
    if (disabled) return
    setSelection((prev) => {
      const next = { ...prev }
      if (!next[sectionId]) next[sectionId] = {}
      next[sectionId] = {
        ...next[sectionId],
        [fieldId]: {
          action: 'customize',
          value,
        },
      }
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {sections.map((section) => {
        const style = sectionStyleMap[section.style ?? 'default']
        return (
          <div
            key={section.id}
            style={{
              background: '#fff',
              borderRadius: 12,
              border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)',
              overflow: 'hidden',
              borderLeft: style.borderLeft,
            }}
          >
            {/* Section header */}
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: '#1a1a1a', display: 'flex', alignItems: 'center', gap: 6, lineHeight: 1.4 }}>
                <span style={{ fontSize: 18 }}>{section.icon ?? style.icon}</span>
                {section.title}
              </div>
              {section.description && (
                <div style={{ fontSize: 12, color: '#999', marginTop: 4, lineHeight: 1.5 }}>
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
                      padding: '8px 0',
                      borderBottom: isLastField ? 'none' : '1px solid #f6f6f6',
                    }}
                  >
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#1a1a1a', marginBottom: 8 }}>
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
                            padding: '6px 8px',
                            background: '#fafafa',
                            borderRadius: 6,
                            marginBottom: 0,
                            fontSize: 13,
                            color: '#262626',
                            lineHeight: 1.5,
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}>
                            {hasExpected && (
                              <div style={{ marginBottom: hasActual ? 6 : 0 }}>
                                {field.expectedLabel && field.actual && (
                                  <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 2 }}>{field.expectedLabel}</div>
                                )}
                                <div>{field.expected || '—'}</div>
                              </div>
                            )}
                            {hasActual && (
                              <div>
                                {field.actualLabel && field.expected && (
                                  <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 2 }}>{field.actualLabel}</div>
                                )}
                                <div>{field.actual || '—'}</div>
                              </div>
                            )}
                          </div>
                        )
                      }

                      // ── Mode 2: COMPARE (dual-column, green-blue) ──
                      if (isInteractive && hasExpected && hasActual) {
                        return (
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 1fr',
                            gap: 10,
                            marginBottom: 10,
                          }}>
                            {hasExpected && (
                              <div style={{ background: '#f6ffed', borderRadius: 6, padding: '6px 8px' }}>
                                <div style={{ fontSize: 11, color: '#87d068', marginBottom: 2 }}>{field.expectedLabel ?? 'AI推荐'}</div>
                                <div style={{ fontSize: 12, color: '#1a1a1a', lineHeight: 1.5, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                                  {field.expected || '—'}
                                </div>
                              </div>
                            )}
                            {hasActual && (
                              <div style={{ background: '#e6f7ff', borderRadius: 6, padding: '6px 8px' }}>
                                <div style={{ fontSize: 11, color: '#1677ff', marginBottom: 2 }}>{field.actualLabel ?? '当前'}</div>
                                <div style={{ fontSize: 12, color: '#1a1a1a', lineHeight: 1.5, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                                  {field.actual || '—'}
                                </div>
                              </div>
                            )}
                          </div>
                        )
                      }

                      // ── Mode 3: REFERENCE (full-width grey block + actions) ──
                      if (isInteractive && hasExpected && !hasActual) {
                        return (
                          <div style={{ marginBottom: 10 }}>
                            <div style={{
                              padding: '10px 12px',
                              background: '#fafafa',
                              borderRadius: 6,
                              marginBottom: 10,
                              fontSize: 13,
                              color: '#262626',
                              lineHeight: 1.6,
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                            }}>
                              {field.expectedLabel && (
                                <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>{field.expectedLabel}</div>
                              )}
                              <div>{field.expected || '—'}</div>
                            </div>
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
                          const colorMap: Record<string, { bg: string; text: string; border: string }> = {
                            primary: { bg: '#1677ff', text: '#fff', border: '#1677ff' },
                            danger: { bg: '#fff', text: '#ff4d4f', border: '#ff4d4f' },
                            default: { bg: '#f5f5f5', text: '#595959', border: '#d9d9d9' },
                          }
                          const c = colorMap[action.type ?? 'default'] ?? colorMap.default
                          return (
                            <button
                              key={action.key}
                              onClick={() => handleActionClick(section.id, field.id, action.key)}
                              disabled={disabled}
                              style={{
                                padding: '5px 12px',
                                borderRadius: 6,
                                border: `1px solid ${isSelected ? c.border : '#d9d9d9'}`,
                                background: isSelected ? c.bg : '#fff',
                                color: isSelected ? c.text : '#595959',
                                fontSize: 13,
                                cursor: disabled ? 'not-allowed' : 'pointer',
                                opacity: disabled ? 0.5 : 1,
                                fontWeight: isSelected ? 500 : 400,
                                transition: 'all 0.15s',
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
                          border: '1px solid #e8e8e8',
                          borderRadius: 6,
                          padding: '8px 10px',
                          fontSize: 13,
                          color: '#1a1a1a',
                          resize: 'none',
                          outline: 'none',
                          boxSizing: 'border-box',
                          ...(isCustomActive ? { borderColor: '#1677ff' } : { background: '#fafafa' }),
                          cursor: disabled || (!isCustomActive && !!field.actions) ? 'not-allowed' : 'text',
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
      // Skip fields with no actions and not customizable
      if (!field.actions?.length && !field.customizable) continue
      // Fields with actions that must be selected
      if (field.actions?.length && !selection[section.id]?.[field.id]?.action) {
        return `「${section.title}」中「${field.label}」请选择处理意见`
      }
      // Customizable fields in customize mode must have value
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
        // For accept/keep actions, derive value from expected/actual so that
        // downstream saveAs templates (e.g. workflowData.gdg_confirmed_asset)
        // receive a concrete string instead of null.
        let derivedValue = sel.value ?? actionDef?.autoFill ?? (
          sel.action === 'accept' ? field.expected
          : sel.action === 'keep' ? field.actual
          : null
        )
        // Defensive: customize with empty/whitespace input should fall back to
        // autoFill or expected so that saveAs never writes "", which breaks
        // downstream default-filter fallbacks ("" does not trigger default).
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
          // If resolved with detail already, hydrate selection state for display
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
                      action: fd.action,
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

      // Section mode validation
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
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 32, height: 32, border: '3px solid #1677ff', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto 12px' }} />
          <p style={{ color: '#999', fontSize: 14 }}>加载审批信息...</p>
          <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
        </div>
      </div>
    )
  }

  // ── Error ────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
        <div style={{ background: '#fff', borderRadius: 12, padding: 24, textAlign: 'center', maxWidth: 320, width: '100%', border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)' }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>🔒</div>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: '#1a1a1a', marginBottom: 8 }}>无法访问审批</h2>
          <p style={{ fontSize: 13, color: '#999', margin: 0 }}>{error}</p>
          <p style={{ fontSize: 12, color: '#ccc', marginTop: 12 }}>该链接可能已过期或无效</p>
        </div>
      </div>
    )
  }

  if (!data) return null

  const isResolved = data.status === 'approved' || data.status === 'rejected'
  const canAct = data.status === 'pending'

  // Approver display
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

  const statusIcon: Record<ApprovalStatus, string> = { pending: '⏳', approved: '✅', rejected: '❌' }
  const copy = approvalDisplay(data.approvalType, data.display)
  const statusCopy = data.status === 'pending' ? copy.pendingText : data.status === 'approved' ? copy.approvedText : copy.rejectedText
  const elapsedEnd = data.status === 'pending' ? Math.floor(Date.now() / 1000) : data.resolvedAt
  const elapsedMs = data.createdAt > 0 && elapsedEnd != null && elapsedEnd >= data.createdAt
    ? (elapsedEnd - data.createdAt) * 1000
    : null

  function renderCardFieldValue(field: CardField): any {
    if (field.label === '申请单' && field.value) {
      return (
        <a
          href={field.value}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#1677ff', fontSize: 14, textDecoration: 'none' }}
          onClick={(e) => e.stopPropagation()}
        >
          点击打开申请单 →
        </a>
      )
    }
    if (field.value.length > 400) {
      return <details><summary style={{ cursor: 'pointer', color: '#486384', fontWeight: 400 }}>
        {field.value.slice(0, 160)}… <span style={{ color: '#2563a6' }}>展开完整内容</span>
      </summary><div style={{ marginTop: 12, maxHeight: 360, overflow: 'auto', fontWeight: 400 }}>{field.value}</div></details>
    }
    return field.value || '—'
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f7f8fa', padding: '20px 12px', color: '#243247' }}>
      <div style={{ maxWidth: 560, margin: '0 auto', overflowWrap: 'anywhere' }}>
        {/* ── Title Card ─────────────────────────────────────────────── */}
        <div style={{
          background: '#fff',
          borderRadius: 12,
          border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)',
          marginBottom: 12,
          overflow: 'hidden',
        }}>
          {/* Header bar */}
          <div style={{
            background: '#edf4ff', borderTop: '3px solid #356fc4', borderBottom: '1px solid #dce7f7',
            padding: '20px',
            color: '#243b60',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 13, opacity: 0.85 }}>{copy.subtitle}</span>
              <span style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                background: '#fff', color: '#315a8f', border: '1px solid #d5e3f4',
                borderRadius: 12,
                padding: '2px 10px',
                fontSize: 12,
              }}>
                {statusIcon[data.status]} {statusCopy}
              </span>
            </div>
            <h1 style={{ fontSize: 17, fontWeight: 600, margin: 0, lineHeight: 1.4 }}>
              {copy.title ?? (data.message || data.workflowTitle || '审批请求')}
            </h1>
          </div>

          {/* Section mode — interactive section cards */}
          {isSectionMode && data.sections && (
            <div style={{ padding: '12px 16px' }}>
              <SectionCard
                sections={data.sections}
                selection={sectionSelection}
                setSelection={setSectionSelection}
                disabled={!canAct}
              />
            </div>
          )}

          {/* Traditional mode — stacked card fields */}
          <div style={{ padding: '12px 16px', background: '#fafbfd', borderBottom: '1px solid #edf0f5', fontSize: 12, color: '#65758b' }}>
            {data.status === 'pending' ? '等待确认' : '确认耗时'} · {elapsedMs === null ? '—' : elapsedMs === 0 ? '0s' : formatDuration(elapsedMs)}
          </div>
          {!isSectionMode && data.cardFields.length > 0 && (
            <div style={{ padding: '12px 16px' }}>
              {data.cardFields.map((field, i) => (
                <div key={i} style={{
                  padding: '10px 0',
                  borderBottom: i < data.cardFields.length - 1 ? '1px solid #f0f0f0' : 'none',
                }}>
                  <div style={{ fontSize: 13, color: '#999', marginBottom: 4 }}>{field.label}</div>
                  <div style={{ fontSize: 14, color: '#1a1a1a', fontWeight: 500, lineHeight: 1.6, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
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
            background: '#fff',
            borderRadius: 12,
            border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)',
            marginBottom: 12,
          }}>
            {/* Approver section */}
            <div style={{ padding: '14px 16px', borderBottom: '1px solid #f0f0f0' }}>
              <div style={{ fontSize: 13, color: '#999', marginBottom: 8 }}>审批人</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {approverList.map((a) => (
                  <span key={a.id} style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '4px 10px',
                    borderRadius: 14,
                    fontSize: 13,
                    background: a.approved
                      ? '#f6ffed'
                      : a.rejected
                        ? '#fff2f0'
                        : '#f5f5f5',
                    color: a.approved
                      ? '#52c41a'
                      : a.rejected
                        ? '#ff4d4f'
                        : '#595959',
                    border: a.isCurrent ? '2px solid #1677ff' : '1px solid transparent',
                  }}>
                    {a.approved && '✓ '}
                    {a.rejected && '✗ '}
                    {a.name}
                    {a.isCurrent && <span style={{ fontSize: 11, color: '#1677ff' }}>(我)</span>}
                  </span>
                ))}
              </div>
            </div>

            {/* Policy */}
            <div style={{ padding: '10px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, color: '#999' }}>审批策略</span>
              <span style={{ fontSize: 13, color: '#595959' }}>{policyLabel[data.approvalPolicy] ?? data.approvalPolicy}</span>
            </div>

            {/* Workflow info */}
            <div style={{ padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, color: '#999' }}>发起时间</span>
              <span style={{ fontSize: 13, color: '#595959' }}>{formatTime(data.createdAt)}</span>
            </div>

            {data.resolvedAt && (
              <div style={{ padding: '10px 16px', borderTop: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: '#999' }}>
                  {data.status === 'approved' ? '通过时间' : '拒绝时间'}
                </span>
                <span style={{ fontSize: 13, color: '#595959' }}>{formatTime(data.resolvedAt)}</span>
              </div>
            )}
          </div>
        )}

        {/* ── Action Card ────────────────────────────────────────────── */}
        {canAct && (
          <div style={{
            background: '#fff',
            borderRadius: 12,
            border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)',
            marginBottom: 12,
            padding: 16,
          }}>
            {/* Validation error */}
            {validationError && isSectionMode && (
              <div style={{
                background: '#fff2f0',
                border: '1px solid #ffccc7',
                borderRadius: 8,
                padding: '8px 12px',
                marginBottom: 12,
                fontSize: 13,
                color: '#ff4d4f',
              }}>
                {validationError}
              </div>
            )}

            {/* Comment */}
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={copy.notePlaceholder}
              aria-label={copy.notePlaceholder}
              rows={isSectionMode ? 2 : 2}
              style={{
                width: '100%',
                border: '1px solid #e8e8e8',
                borderRadius: 8,
                padding: '8px 12px',
                fontSize: 14,
                color: '#1a1a1a',
                resize: 'none',
                outline: 'none',
                marginBottom: 12,
                boxSizing: 'border-box',
              }}
              onFocus={(e) => { e.target.style.borderColor = '#1677ff' }}
              onBlur={(e) => { e.target.style.borderColor = '#e8e8e8' }}
            />

            {/* Buttons */}
            <div style={{ display: 'flex', gap: 12 }}>
              <button
                onClick={() => handleAction('reject')}
                disabled={actionLoading}
                style={{
                  flex: 1,
                  minHeight: 44, padding: '8px 12px',
                  borderRadius: 8,
                  border: '1px solid #ff4d4f',
                  background: '#fff',
                  color: '#ff4d4f',
                  fontSize: 15,
                  fontWeight: 500,
                  cursor: actionLoading ? 'not-allowed' : 'pointer',
                  opacity: actionLoading ? 0.5 : 1,
                }}
              >
                {copy.rejectLabel}
              </button>
              <button
                onClick={() => handleAction('approve')}
                disabled={actionLoading}
                style={{
                  flex: 1,
                  minHeight: 44, padding: '8px 12px',
                  borderRadius: 8,
                  border: 'none',
                  background: '#1677ff',
                  color: '#fff',
                  fontSize: 15,
                  fontWeight: 500,
                  cursor: actionLoading ? 'not-allowed' : 'pointer',
                  opacity: actionLoading ? 0.5 : 1,
                }}
              >
                {actionLoading ? '处理中...' : copy.confirmLabel}
              </button>
            </div>
          </div>
        )}

        {/* ── Resolved status ────────────────────────────────────────── */}
        {isResolved && (
          <div style={{
            background: '#fff',
            borderRadius: 12,
            border: '1px solid #e4e9f0', boxShadow: '0 3px 12px rgba(24,39,61,0.035)',
            marginBottom: 12,
            padding: '20px 16px',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>
              {data.status === 'approved' ? '✅' : '❌'}
            </div>
            <div style={{ fontSize: 15, fontWeight: 500, color: data.status === 'approved' ? '#52c41a' : '#ff4d4f' }}>
              {data.status === 'approved'
                ? copy.approvedText
                : copy.rejectedText}
            </div>
            {isSectionMode && data.detail && (
              <div style={{ marginTop: 12, textAlign: 'left' }}>
                {data.sections?.map((section) => {
                  const secDetail = data.detail?.[section.id] as Record<string, { actionLabel?: string; value?: string | null }> | undefined
                  if (!secDetail) return null
                  return (
                    <div key={section.id} style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: '#595959' }}>{section.title}</div>
                      {section.fields.map((field) => {
                        const fd = secDetail[field.id]
                        if (!fd) return null
                        return (
                          <div key={field.id} style={{ fontSize: 12, color: '#999', paddingLeft: 12, marginTop: 2 }}>
                            {field.label}: {fd.actionLabel}
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
            borderRadius: 8,
            padding: '10px 14px',
            fontSize: 13,
            background: result.error ? '#fff2f0' : '#f6ffed',
            color: result.error ? '#ff4d4f' : '#52c41a',
            marginBottom: 12,
          }}>
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
        <div style={{ textAlign: 'center', padding: '8px 0', fontSize: 11, color: '#ccc' }}>
          {copy.footer}
        </div>
      </div>
    </div>
  )
}
