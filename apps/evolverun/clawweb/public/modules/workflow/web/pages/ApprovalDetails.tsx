type ApprovalDetailsData = {
  approverIds: string[]
  approverNames: string[]
  approvedBy: string[]
  rejectedBy: string[]
  approvalType: string | null
  approvalPolicy: string
  status: string
  createdAt: number
  resolvedAt: number | null
}

function formatTime(ts: number | null): string {
  if (!ts) return '—'
  const ms = ts > 1e12 ? ts : ts * 1000
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

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

export function ApprovalDetails({ data, colors: COLOR }: {
  data: ApprovalDetailsData
  colors: Record<string, string>
}) {
  const approverList = data.approverIds.map((id, i) => ({
    id,
    name: data.approverNames[i]?.trim() || id,
    approved: data.approvedBy.includes(id),
    rejected: data.rejectedBy.includes(id),
    isCurrent: false,
  }))

  const policyLabel: Record<string, string> = {
    any: '任一审批人通过即可',
    all: '需全部审批人通过',
    majority: '多数审批人通过即可',
  }

  return (
    <>
      {(approverList.length > 0 || !['SUPPLEMENT_COMPLETE', 'HUMAN_CONFIRM'].includes(data.approvalType ?? '')) && (
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
            <div style={{ fontSize: 12, color: COLOR.textTertiary, marginBottom: 10, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 0.5 }}>可处理人员</div>
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
                    {a.name !== a.id && <span style={{ fontSize: 12, color: COLOR.textSecondary }}>{a.id}</span>}
                    {a.isCurrent && <span style={{ fontSize: 11, color: COLOR.primary, fontWeight: 500 }}>(我)</span>}
                  </div>
                )
              })}
            </div>
          </div>

          {!['SUPPLEMENT_COMPLETE', 'HUMAN_CONFIRM'].includes(data.approvalType ?? '') && <>
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
          </>}
        </div>
      )}
    </>
  )
}
