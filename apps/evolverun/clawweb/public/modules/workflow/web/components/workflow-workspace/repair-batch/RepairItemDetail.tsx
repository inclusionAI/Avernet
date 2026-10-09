import { useEffect, useRef, useState } from 'react'
import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { button, exclusion, isLegacyPreview, itemTitle, JsonDetails, states } from './repair-view'

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(row => !!row && typeof row === 'object' && !Array.isArray(row)) : []
}
const fieldNames: Record<string, string> = {
  prompt: 'Prompt 内容', timeoutMs: '硬超时（毫秒）', timeoutSeconds: '超时（秒）',
  maxAttempts: '最大尝试次数', backoffMs: '重试间隔（毫秒）',
}
function fieldLabel(operation: Record<string, unknown>) {
  const path = String(operation.path ?? operation.op ?? '配置')
  return fieldNames[path.split(/[/.]/).at(-1) ?? path] ?? path
}
export function changeSummary(item: RepairInboxItem) {
  const operations = records(item.proposal?.operations)
  if (!operations.length) return '文字建议，详情中查看修复要求'
  return operations.slice(0, 3).map(operation => {
    const value = operation.value
    const suffix = operation.op === 'remove' ? '（删除）'
      : typeof value === 'number' || typeof value === 'boolean' ? ` → ${value}` : ''
    return `${String(operation.nodeId ?? '工作流')} · ${fieldLabel(operation)}${suffix}`
  }).join('；') + (operations.length > 3 ? `；另 ${operations.length - 3} 项` : '')
}
export function sourceRunCount(item: RepairInboxItem): number | null {
  return new Set(item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : [])).size || null
}
function ChangeValue({ value }: { value: unknown }) {
  const [copied, setCopied] = useState('')
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  useEffect(() => { setCopied('') }, [text])
  return <div>
    <div className="flex items-start rounded-lg bg-slate-50">
    <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words p-3 text-xs leading-6 text-slate-700">{text}</pre>
    <button type="button" aria-label="复制目标值" title={copied || '复制目标值'}
      className="mr-2 mt-2 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-slate-200 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
      onClick={async () => {
      try { await navigator.clipboard.writeText(text ?? ''); setCopied('已复制') }
      catch { setCopied('复制失败，请手动选择内容') }
    }}><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      {copied === '已复制' ? <path d="m5 12 4 4L19 6" /> : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>}
    </svg></button>
    </div>
    {copied && <span role="status" className={copied === '已复制' ? 'sr-only' : 'text-xs text-red-700'}>{copied}</span>}
  </div>
}

export default function RepairItemDetail({ item, detail, loading, error, onLoad, selected, onToggle, canEdit, limitReached, onDisposition, showSelection = true, embedded = false }: {
  item: RepairInboxItem; detail?: RepairInboxItem; loading?: boolean; error?: string;
  onLoad: (id: string) => void; selected: boolean; onToggle: (id: string) => void; canEdit: boolean;
  limitReached: boolean; onDisposition: (item: RepairInboxItem, action: 'no_action' | 'restore') => void;
  showSelection?: boolean;
  embedded?: boolean;
}) {
  const loader = useRef(onLoad)
  loader.current = onLoad
  useEffect(() => { loader.current(item.itemId) }, [item.itemId])
  const shown = detail ?? item
  const reason = exclusion(item)
  const operations = records(shown.proposal?.operations)
  const diagnoses = records(detail?.context?.diagnoses)
  const runs = sourceRunCount(shown)
  return <div className="space-y-5">
    {!embedded && <header className="space-y-2">
      <p className="text-xs text-slate-500">{states[item.state]} · {runs === null ? '来源运行数未知' : `本建议来源覆盖 ${runs} 个运行`}</p>
      <h4 className="text-base font-semibold leading-7 text-slate-900">{itemTitle(shown)}</h4>
      {showSelection && <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" className="h-4 w-4 accent-blue-600"
        aria-label={`选择 ${itemTitle(item)}`} checked={selected} disabled={!canEdit || !!reason || (!selected && limitReached)} onChange={() => onToggle(item.itemId)} />纳入本次修复</label>}
      {reason && <p className="text-xs text-amber-700">{reason}</p>}
    </header>}
    <section aria-label="修改内容" className="space-y-3 border-t border-slate-100 pt-4">
      <h4 className="text-sm font-semibold text-slate-900">修改内容</h4>
      <p className="text-xs leading-5 text-slate-500">以下为建议目标值，不是与当前部署版本的对比；生成 Pack 后仍需审阅实际 diff。</p>
      {operations.map((operation, index) => <div key={index} className="space-y-2">
        <p className="text-sm font-medium text-slate-800">{String(operation.nodeId ?? '工作流')} · {fieldLabel(operation)}{operation.op === 'remove' ? '（删除）' : ''}</p>
        <p className="break-all font-mono text-xs text-slate-400">{String(operation.path ?? '')}</p>
        {Object.hasOwn(operation, 'value') && (typeof operation.value === 'string' && operation.value.length > 160
          ? <details><summary className="cursor-pointer text-xs font-medium text-blue-700">展开完整目标值</summary><ChangeValue value={operation.value} /></details>
          : <ChangeValue value={operation.value} />)}
      </div>)}
      {!operations.length && <p className="text-xs text-slate-500">尚无结构化修改明细；生成草稿后仍需审阅实际 diff。</p>}
      {shown.instruction && shown.instruction !== itemTitle(shown) && <p className="whitespace-pre-wrap text-sm leading-6 text-slate-600">修复要求：{shown.instruction}</p>}
    </section>
    <section aria-label="判断依据" className="space-y-3 border-t border-slate-100 pt-4">
      <h4 className="text-sm font-semibold text-slate-900">判断依据</h4>
      <p className="text-xs leading-5 text-slate-500">{shown.sources.length} 条来源引用。以下诊断支持这条建议，不代表覆盖整个问题的所有运行。</p>
      {loading && <p role="status" className="text-xs text-slate-500">正在读取该建议的完整内容与依据，不影响浏览问题列表…</p>}
      {error && <div role="alert" className="text-xs text-red-600">建议详情读取失败：{error}<button type="button" className="ml-2 underline" onClick={() => onLoad(item.itemId)}>重试详情</button></div>}
      {!loading && !error && detail && !diagnoses.length && <p className="text-xs text-slate-500">暂无关联诊断正文，保留来源引用供核对。</p>}
      {diagnoses.map((diagnosis, index) => <details key={index} className="rounded-lg border border-slate-200 p-3">
        <summary className="cursor-pointer text-xs leading-5 text-slate-700">{String(diagnosis.nodeId ?? '工作流')} · 来源运行 {index + 1}
          {typeof diagnosis.completedAtMs === 'number' && <span className="ml-2 text-slate-400"> · {new Date(diagnosis.completedAtMs).toLocaleString()}</span>}</summary>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-600">{String(diagnosis.reasoning ?? '未提供原因说明')}</p>
        {records(diagnosis.evidence).map((event, eventIndex) => <div key={eventIndex} className="mt-3 border-l-2 border-slate-200 pl-3 text-xs">
          <p className={event.missing ? 'text-amber-700' : 'text-slate-600'}>{event.missing ? '原始证据已缺失' : String(event.eventType ?? '运行事件')}
            {typeof event.occurredAtMs === 'number' && <> · {new Date(event.occurredAtMs).toLocaleString()}</>}</p>
          <JsonDetails title="原始事件" value={event} />
        </div>)}
      </details>)}
    </section>
    {detail && <JsonDetails title="技术详情（来源标识与完整载荷）" value={detail} />}
    {item.disposition && <p className="text-xs text-slate-500">处置记录：{item.disposition.reason}</p>}
    {canEdit && !isLegacyPreview(item) && (item.state === 'pending' || item.state === 'no_action') && <button type="button" className={button}
      aria-label={`${item.state === 'no_action' ? '恢复' : '暂不处理'} ${itemTitle(item)}`}
      onClick={() => onDisposition(item, item.state === 'no_action' ? 'restore' : 'no_action')}>{item.state === 'no_action' ? '恢复待处理' : '暂不处理'}</button>}
  </div>
}
