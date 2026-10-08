import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { button, exclusion, isLegacyPreview, itemTitle, JsonDetails, states } from './repair-view'

export function RepairStateCounts({ items }: { items: RepairInboxItem[] }) {
  return <span className="flex flex-wrap gap-2 text-xs text-slate-500">{Object.entries(states)
    .filter(([state]) => items.some(item => item.state === state))
    .map(([state, label]) => <span key={state}>{label} {items.filter(item => item.state === state).length}</span>)}</span>
}

function RepairChange({ item }: { item: RepairInboxItem }) {
  const operations: Record<string, unknown>[] = Array.isArray(item.proposal?.operations)
    ? item.proposal.operations.filter((value): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)) : []
  if (!operations.length) return <p className="mt-2 text-xs text-slate-500">尚无结构化修改明细；生成草稿后仍需审阅实际 diff。</p>
  return <ul aria-label="建议修改内容" className="mt-2 space-y-1 rounded-md bg-slate-50 p-2 text-xs text-slate-600">
    {operations.map((operation, index) => {
      const value = typeof operation.value === 'string' ? operation.value : JSON.stringify(operation.value)
      return <li key={index} className="break-words">
      <span className="font-medium">{String(operation.nodeId ?? '工作流')}</span>
      {' · '}{String(operation.path ?? operation.op ?? '配置')}
      {Object.hasOwn(operation, 'value') && <> → <code className="whitespace-pre-wrap">{value?.slice(0, 160)}{value && value.length > 160 ? '…（完整内容见证据）' : ''}</code></>}
      {operation.op === 'remove' && '（删除）'}
    </li>})}
  </ul>
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(row => !!row && typeof row === 'object' && !Array.isArray(row)) : []
}

function RepairEvidence({ item }: { item: RepairInboxItem }) {
  const diagnoses = records(item.context?.diagnoses)
  return <div className="max-h-80 space-y-2 overflow-y-auto">
    {diagnoses.map((diagnosis, index) => <details key={index} className="rounded border border-slate-200 p-2">
      <summary className="cursor-pointer text-slate-700">来源诊断 {index + 1} · {String(diagnosis.nodeId ?? '工作流')}
        {typeof diagnosis.completedAtMs === 'number' && <> · {new Date(diagnosis.completedAtMs).toLocaleString()}</>}</summary>
      <p className="mt-2 whitespace-pre-wrap leading-5 text-slate-600">{String(diagnosis.reasoning ?? '未提供原因说明')}</p>
      {records(diagnosis.evidence).map((event, eventIndex) => <div key={eventIndex} className="mt-2 border-l-2 border-slate-200 pl-2">
        <p className={event.missing ? 'text-amber-700' : 'text-slate-600'}>{event.missing ? '原始证据已缺失' : String(event.eventType ?? '运行事件')}
          {typeof event.occurredAtMs === 'number' && <> · {new Date(event.occurredAtMs).toLocaleString()}</>}</p>
        <JsonDetails title="事件内容与标识" value={event} />
      </div>)}
      <p className="mt-2 break-all text-slate-400">运行 {String(diagnosis.flowId ?? '未知')} · 分析 {String(diagnosis.analysisId ?? '未知')}</p>
    </details>)}
    {!diagnoses.length && <p className="text-slate-500">暂无关联诊断正文，保留来源引用供核对。</p>}
  </div>
}

function runCount(items: RepairInboxItem[]): number | null {
  const runs = new Set(items.flatMap(item => item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : [])))
  return runs.size || null
}

export default function RepairItems({ items, allItems = items, selected, onToggle, canEdit, limit, taskId, onDisposition,
  details, detailLoading, detailErrors, onLoadDetail, embedded = false }: {
  items: RepairInboxItem[]; allItems?: RepairInboxItem[]; selected: string[]; onToggle: (id: string) => void;
  canEdit: boolean; limit: number; taskId?: string; onDisposition?: (item: RepairInboxItem, action: 'no_action' | 'restore') => void;
  details?: Record<string, RepairInboxItem>; detailLoading?: Record<string, boolean>; detailErrors?: Record<string, string>;
  onLoadDetail?: (itemId: string) => void; embedded?: boolean;
}) {
  return <div className="space-y-3">
    {!embedded && <div className="flex flex-wrap items-center gap-3 py-2 text-xs text-slate-600">
      <span>本页修复建议 · {items.length} 项</span><RepairStateCounts items={allItems} />
    </div>}
    {items.map((item, index) => {
      const reason = exclusion(item, taskId)
      const title = itemTitle(item)
      const detail = details?.[item.itemId] ?? item
      const runs = runCount([item])
      return <article key={item.itemId} className={`rounded-lg border p-3 ${selected.includes(item.itemId) ? 'border-blue-200 bg-blue-50/30' : 'border-slate-200 bg-white'}`}>
        <div className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-blue-600" aria-label={`选择 ${title}`}
          checked={selected.includes(item.itemId)} disabled={!canEdit || !!reason || (!selected.includes(item.itemId) && selected.length >= limit)} onChange={() => onToggle(item.itemId)} />
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><p className="break-words text-sm font-medium text-slate-800"><span className="mr-2 text-xs font-normal text-slate-500">建议 {index + 1}</span>{title}</p><span className="text-xs text-slate-500">{states[item.state]}</span></div>
            <RepairChange item={item} />
            <p className="mt-2 text-xs text-slate-500">{runs === null ? '来源运行数未知' : `本建议来源覆盖 ${runs} 个运行`} · {item.sources.length} 条来源引用</p>
            {reason && <p className="text-xs text-amber-700">{reason}</p>}
            <details className="mt-2 text-xs" onToggle={event => { if (event.currentTarget.open) onLoadDetail?.(item.itemId) }}>
              <summary className="cursor-pointer text-slate-500">查看建议与证据</summary>
              {detailLoading?.[item.itemId] ? <p role="status" className="mt-2 text-slate-500">加载证据…</p>
                : detailErrors?.[item.itemId] ? <p role="alert" className="mt-2 text-red-600">证据读取失败：{detailErrors[item.itemId]}</p>
                  : <div className="mt-2 space-y-2">
                    {detail.instruction && <p className="whitespace-pre-wrap leading-5 text-slate-600"><strong>修复要求：</strong>{detail.instruction}</p>}
                    <RepairEvidence item={detail} />
                    <JsonDetails title="来源引用与标识" value={detail.sources} />
                    <JsonDetails title="技术数据（完整载荷）" value={{ instruction: detail.instruction, proposal: detail.proposal, sources: detail.sources, context: detail.context }} />
                  </div>}

            </details>
            {item.disposition && <p className="mt-2 text-xs text-slate-500">处置记录：{item.disposition.reason}</p>}
            {canEdit && onDisposition && !isLegacyPreview(item) && (item.state === 'pending' || item.state === 'no_action') && <button type="button" className={`${button} mt-2`}
              aria-label={`${item.state === 'no_action' ? '恢复' : '暂不处理'} ${title}`} onClick={() => onDisposition(item, item.state === 'no_action' ? 'restore' : 'no_action')}>
              {item.state === 'no_action' ? '恢复待处理' : '暂不处理'}
            </button>}
          </div>
        </div>
      </article>
    })}</div>
}
