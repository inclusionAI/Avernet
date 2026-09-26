import { useState } from 'react'
import { HealthScoreCard } from '../HealthScoreCard'
import {
  MOCK_WEAK_LINKS,
  MOCK_REMEDIES,
  MOCK_SUGGESTIONS,
  type Remedy,
  type Suggestion,
} from './evolution-mock'

type EvoTab = 'health' | 'diagnosis' | 'remedies' | 'suggestions'

const EVO_TABS: { key: EvoTab; label: string }[] = [
  { key: 'health', label: '健康度' },
  { key: 'diagnosis', label: '诊断' },
  { key: 'remedies', label: '经验' },
  { key: 'suggestions', label: '建议' },
]

const REMEDY_STATUS: Record<Remedy['status'], { label: string; cls: string }> = {
  draft: { label: '草稿', cls: 'bg-gray-100 text-gray-600' },
  verified: { label: '已验证', cls: 'bg-blue-50 text-blue-700' },
  published: { label: '已上线', cls: 'bg-emerald-50 text-emerald-700' },
  retired: { label: '已失效', cls: 'bg-gray-100 text-gray-400' },
}

const REMEDY_KIND: Record<Remedy['kind'], string> = {
  kb_hint: '提示',
  prompt_patch: '提示词补丁',
  arg_template_fix: '参数模板修正',
  node_patch: '节点结构补丁',
  alert: '告警',
}

function MockBadge() {
  return (
    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">
      演示数据
    </span>
  )
}

function DiagnosisPanel({ onGenerate }: { onGenerate: (nodeName: string) => void }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-medium text-gray-900">
          失败热点 & 诊断
          <MockBadge />
        </h3>
        <button
          onClick={() => onGenerate('全部弱点')}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
        >
          生成进化建议
        </button>
      </div>
      {MOCK_WEAK_LINKS.map((link) => (
        <div key={link.nodeId} className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-gray-400">#{link.rank}</span>
                <span className="text-sm font-medium text-gray-900">{link.nodeName}</span>
                {link.hasRemedy ? (
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                    已有 remedy
                  </span>
                ) : (
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500">
                    无 remedy
                  </span>
                )}
              </div>
              <p className="font-mono text-xs text-gray-500">{link.signature}</p>
              <p className="text-xs text-gray-500">
                影响 {link.impactRuns} 次运行 · 失败率 {link.failureRate}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600">
                {link.failureMode}
              </span>
              {!link.hasRemedy && (
                <button
                  onClick={() => onGenerate(link.nodeName)}
                  className="rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100"
                >
                  生成补丁
                </button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function RemediesPanel() {
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-900">已关联经验</h3>
        <MockBadge />
        <p className="text-xs text-gray-500">全局 + 当前工作流范围内的 remedy</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">ID</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">失败签名</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">修法</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">范围</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">状态</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">命中/救回</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">创建时间</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {MOCK_REMEDIES.map((r) => (
              <tr key={r.id}>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-gray-900">{r.id}</td>
                <td className="max-w-[220px] truncate px-4 py-2 font-mono text-xs text-gray-600" title={r.signature}>
                  {r.signature}
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
                  {REMEDY_KIND[r.kind] ?? r.kind}
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">{r.scope}</td>
                <td className="whitespace-nowrap px-4 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${REMEDY_STATUS[r.status].cls}`}>
                    {REMEDY_STATUS[r.status].label}
                  </span>
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
                  {r.hits} / {r.rescued}
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{r.createdAt}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

type SuggestionStatus = 'pending' | 'adopted' | 'benched' | 'rejected'

function SuggestionCard({
  suggestion,
  status,
  onAction,
}: {
  suggestion: Suggestion
  status: SuggestionStatus
  onAction: (id: string, action: Exclude<SuggestionStatus, 'pending'>) => void
}) {
  const [showEvidence, setShowEvidence] = useState(false)

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col justify-between gap-2 md:flex-row md:items-center">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900">{suggestion.weakNode}</span>
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600">{suggestion.kind}</span>
          {status !== 'pending' && (
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                status === 'adopted'
                  ? 'bg-emerald-50 text-emerald-700'
                  : status === 'benched'
                    ? 'bg-blue-50 text-blue-700'
                    : 'bg-gray-100 text-gray-500'
              }`}
            >
              {status === 'adopted' ? '已采纳' : status === 'benched' ? '已创建 Bench' : '已拒绝'}
            </span>
          )}
        </div>
        <span className="text-xs text-gray-500">影响 {suggestion.impactRuns} 次运行</span>
      </div>

      <p className="mt-2 text-sm text-gray-600">{suggestion.description}</p>
      <p className="mt-1 font-mono text-xs text-gray-400">{suggestion.signature}</p>

      <button
        onClick={() => setShowEvidence((v) => !v)}
        className="mt-2 text-xs text-blue-600 hover:underline"
      >
        {showEvidence ? '收起证据 runs' : '查看证据 runs'}
      </button>
      {showEvidence && (
        <div className="mt-1 flex flex-wrap gap-2">
          {suggestion.evidenceRuns.map((runId) => (
            <span key={runId} className="rounded border border-gray-200 px-1.5 py-0.5 font-mono text-[10px] text-gray-500">
              {runId}
            </span>
          ))}
        </div>
      )}

      {status === 'pending' && (
        <div className="mt-3 flex gap-2">
          <button
            onClick={() => onAction(suggestion.id, 'adopted')}
            className="flex-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
          >
            采纳并应用
          </button>
          <button
            onClick={() => onAction(suggestion.id, 'benched')}
            className="flex-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            跑 bench
          </button>
          <button
            onClick={() => onAction(suggestion.id, 'rejected')}
            className="flex-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            拒绝
          </button>
        </div>
      )}
    </div>
  )
}

interface EvolutionTabProps {
  workflowId: string
}

export default function EvolutionTab({ workflowId }: EvolutionTabProps) {
  const [tab, setTab] = useState<EvoTab>('health')
  const [suggestionStatus, setSuggestionStatus] = useState<Record<string, SuggestionStatus>>({})
  const [notice, setNotice] = useState<string | null>(null)

  const showNotice = (text: string) => {
    setNotice(text)
    window.setTimeout(() => setNotice(null), 2500)
  }

  const handleSuggestionAction = (id: string, action: Exclude<SuggestionStatus, 'pending'>) => {
    setSuggestionStatus((prev) => ({ ...prev, [id]: action }))
    const label = action === 'adopted' ? '已采纳并应用（演示）' : action === 'benched' ? '已创建 Bench 任务（演示）' : '已拒绝（演示）'
    showNotice(`${id} ${label}`)
  }

  return (
    <div>
      <div className="mb-4 flex items-center gap-1 border-b border-gray-200">
        {EVO_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {notice && (
        <div className="mb-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          {notice}
        </div>
      )}

      {tab === 'health' && <HealthScoreCard workflowId={workflowId} />}

      {tab === 'diagnosis' && (
        <DiagnosisPanel onGenerate={(target) => showNotice(`正在为 ${target} 生成进化建议（演示）`)} />
      )}

      {tab === 'remedies' && <RemediesPanel />}

      {tab === 'suggestions' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-gray-900">待审进化建议</h3>
            <MockBadge />
            <p className="text-xs text-gray-500">来自批量分析的弱点清单，需人审后应用/发版</p>
          </div>
          {MOCK_SUGGESTIONS.map((s) => (
            <SuggestionCard
              key={s.id}
              suggestion={s}
              status={suggestionStatus[s.id] ?? 'pending'}
              onAction={handleSuggestionAction}
            />
          ))}
        </div>
      )}
    </div>
  )
}
