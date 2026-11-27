import { useQuery } from '@tanstack/react-query'
import { useFlowApprovals } from '@avernet/clawweb-shared/web/api/hooks'
import type { ApprovalCardSummary } from '@avernet/clawweb-shared/web/api/client'
import { formatTime } from '../utils/time'
import { fetchApprovalJson } from '../pages/approval-session-fetch'

type ApprovalRecord = ApprovalCardSummary & {
  note?: string | null
  comment?: string | null
  detail?: unknown
}

const STATUS_LABELS = new Map([
  ['pending', '待审批'],
  ['approved', '已通过'],
  ['rejected', '已拒绝'],
])

const RETRY_CLASS = 'rounded border border-border px-2 py-0.5 text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50'

export default function ApprovalNodeDetails({ flowId, nodeId, readOnly = false }: { flowId: string; nodeId: string; readOnly?: boolean }) {
  const approvals = useFlowApprovals(flowId)
  const cards = (approvals.data?.items ?? [])
    .filter((card) => card.flowId === flowId && card.nodeId === nodeId)
    .sort((a, b) => b.createdAt - a.createdAt || b.id - a.id)

  return (
    <section aria-label="审批详情" className="space-y-2 text-xs text-foreground">
      <h4 className="font-semibold text-muted-foreground">审批详情</h4>
      {approvals.isLoading && (
        <p role="status" className="rounded-md border border-border bg-muted px-3 py-2 text-muted-foreground">
          加载审批记录…
        </p>
      )}
      {approvals.isError && (
        <div role="alert" className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
          <span>审批记录加载失败</span>
          <button type="button" className={RETRY_CLASS} disabled={approvals.isFetching} onClick={() => { void approvals.refetch() }}>
            重试
          </button>
        </div>
      )}
      {!approvals.isLoading && !approvals.isError && cards.length === 0 && (
        <p className="rounded-md border border-border bg-muted px-3 py-2 text-muted-foreground">暂无审批记录</p>
      )}
      {cards.map((card) => <ApprovalRecordDetails key={card.id} card={card} readOnly={readOnly} />)}
    </section>
  )
}

function ApprovalRecordDetails({ card, readOnly }: { card: ApprovalCardSummary; readOnly: boolean }) {
  const details = useQuery({
    queryKey: ['approval-node-details', card.flowId, card.nodeId, card.id],
    queryFn: ({ signal }) => fetchApprovalJson<ApprovalRecord>(`/api/approval/${card.id}`, { signal }),
    refetchInterval: (query) => (query.state.data?.status ?? card.status) === 'pending' ? 5000 : false,
    retry: false,
  })
  const record = details.data ?? card
  // Structured cards retain the JSON wrapper in comment, even when note is empty.
  const comment = details.data?.detail != null
    ? (details.data.note ?? '')
    : (details.data?.note?.trim() ? details.data.note : details.data?.comment)
  const approvalTime = record.resolvedAt == null
    ? (record.status === 'pending' ? '待审批' : '—')
    : formatTime(record.resolvedAt)
  const href = `/approval/${card.id}`

  return (
    <article aria-label={`审批记录 #${card.id}`} className="overflow-hidden rounded-md border border-border bg-card">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted px-3 py-2">
        <h5 className="font-medium">审批记录 #{card.id}</h5>
        <span className="text-muted-foreground">创建于 {formatTime(record.createdAt)}</span>
      </header>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-4 gap-y-2 px-3 py-3">
        <dt className="text-muted-foreground">审批人</dt>
        <dd className="min-w-0">
          {record.approverIds.length === 0 ? '—' : (
            <ul className="flex flex-wrap gap-1.5">
              {record.approverIds.map((id, index) => {
                const name = record.approverNames[index]?.trim() || id
                const decision = record.approvedBy.includes(id)
                  ? '已通过'
                  : record.rejectedBy.includes(id)
                    ? '已拒绝'
                    : record.status === 'pending' ? '待审批' : '未处理'
                return (
                  <li key={`${id}-${index}`} className="inline-flex flex-wrap items-baseline gap-1 rounded border border-border bg-muted px-2 py-0.5">
                    <span className="break-all">{name}</span>
                    {name !== id && <span className="break-all font-mono text-muted-foreground">({id})</span>}
                    <span className="text-muted-foreground">· {decision}</span>
                  </li>
                )
              })}
            </ul>
          )}
        </dd>
        <dt className="text-muted-foreground">审批时间</dt>
        <dd>{approvalTime}</dd>
        <dt className="text-muted-foreground">审批意见</dt>
        <dd className="min-w-0">
          {details.isLoading ? (
            <span role="status" className="text-muted-foreground">加载审批意见…</span>
          ) : details.isError ? (
            <div role="alert" className="flex flex-wrap items-center gap-3">
              <span>审批详情加载失败</span>
              <button type="button" className={RETRY_CLASS} disabled={details.isFetching} onClick={() => { void details.refetch() }}>
                重试
              </button>
            </div>
          ) : (
            <p className="whitespace-pre-wrap break-words">{comment?.trim() ? comment : '暂无审批意见'}</p>
          )}
        </dd>
        <dt className="text-muted-foreground">审批状态</dt>
        <dd>{STATUS_LABELS.get(record.status) ?? record.status}</dd>
        <dt className="text-muted-foreground">审批链接</dt>
        <dd className="min-w-0">
          {readOnly ? (
            <span className="text-muted-foreground">只读视图不提供审批入口</span>
          ) : (
            <a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {href}
            </a>
          )}
        </dd>
      </dl>
    </article>
  )
}
