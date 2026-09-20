/**
 * RunArchivePanel — 运行档案 Tab 组件
 *
 * 展示失败工作流的完整运行档案，包含 4 个部分：
 * 1. 运行过程数据（流程信息、节点执行、事件流、步骤追踪、运行日志）
 * 2. 错误详情（失败节点卡片，含 input/output/prompt/session/trace）
 * 3. AI 诊断（根因、分类、建议、patch）
 * 4. 建议的工作流 YAML（双栏对比 diff）
 */
import { useState, useMemo } from 'react'
import { useRunArchive } from '@avernet/workflow/web/api/hooks'
import type { RunArchiveData, RunArchiveDiagnosis } from '@avernet/clawweb-shared/web/types'

// ── Helpers ──

function formatTime(ts: string | number | null): string {
  if (!ts) return '-'
  const d = typeof ts === 'number' ? new Date(ts > 1e12 ? ts : ts * 1000) : new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

function formatDuration(ms: number | null): string {
  if (!ms || !Number.isFinite(ms)) return '-'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.floor((ms % 60_000) / 1000)
  return `${m}m${s}s`
}

const SEVERITY_COLORS: Record<string, string> = {
  low: 'bg-blue-50 text-blue-700',
  medium: 'bg-amber-50 text-amber-700',
  high: 'bg-orange-50 text-orange-700',
  critical: 'bg-red-50 text-red-700',
}

const ANALYSIS_SOURCE_LABEL: Record<string, { label: string; cls: string }> = {
  reused: { label: '复用已有分析', cls: 'bg-emerald-50 text-emerald-700' },
  preview: { label: '自行分析', cls: 'bg-blue-50 text-blue-700' },
  failed: { label: '分析不可用', cls: 'bg-red-50 text-red-700' },
  pending: { label: '待分析', cls: 'bg-gray-100 text-gray-500' },
}

// ── Collapsible Section ──

function Collapsible({
  title,
  children,
  defaultOpen = false,
  badge,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
  badge?: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-t border-gray-100 pt-3 mt-3">
      <button
        className="flex w-full items-center gap-2 text-sm font-medium text-gray-700 hover:text-gray-900"
        onClick={() => setOpen(!open)}
      >
        <span className="text-gray-400 text-xs">{open ? '▼' : '▶'}</span>
        <span>{title}</span>
        {badge}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}

// ── Part 1: Run Process ──

function ProcessInfoPanel({ data }: { data: RunArchiveData }) {
  const flowRun = data.flowRun as Record<string, unknown> | null
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
        <DefItem label="工作流 ID" value={String(flowRun?.workflow_id ?? '-')} />
        <DefItem label="Flow ID" value={String(flowRun?.flow_id ?? '-')} mono />
        <DefItem label="运行状态" value={String(flowRun?.status ?? '-')} />
        <DefItem label="开始时间" value={formatTime(String(flowRun?.started_at ?? ''))} />
        <DefItem label="结束时间" value={formatTime(String(flowRun?.completed_at ?? ''))} />
        <DefItem label="耗时" value={formatDuration(Number(flowRun?.duration_ms ?? null))} />
        <DefItem label="节点总数" value={String(data.nodeExecutions.length)} />
        <DefItem label="失败节点" value={String(data.failureSummary.failedNodeCount)} />
      </div>
      <Collapsible title="工作流规格 (workflow_specs)" badge={<span className="text-xs text-gray-400">spec_json</span>}>
        <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs overflow-x-auto max-h-64">
          {JSON.stringify(data.flowRun ?? {}, null, 2)}
        </pre>
      </Collapsible>
    </div>
  )
}

function NodeExecTable({ data }: { data: RunArchiveData }) {
  const [filter, setFilter] = useState<'all' | 'failed' | 'success'>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const nodes = useMemo(() => {
    const list = data.nodeExecutions as Array<Record<string, unknown>>
    if (filter === 'failed') return list.filter(n => n.status === 'failed')
    if (filter === 'success') return list.filter(n => n.status !== 'failed')
    return list
  }, [data.nodeExecutions, filter])

  return (
    <div>
      <div className="flex gap-2 mb-3">
        <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} label={`全部(${data.nodeExecutions.length})`} />
        <FilterChip active={filter === 'failed'} onClick={() => setFilter('failed')} label={`失败(${data.failureSummary.failedNodeCount})`} />
        <FilterChip active={filter === 'success'} onClick={() => setFilter('success')} label={`成功(${data.nodeExecutions.length - data.failureSummary.failedNodeCount})`} />
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-500">
            <tr>
              <th className="px-4 py-2 text-left">节点</th>
              <th className="px-4 py-2 text-left">状态</th>
              <th className="px-4 py-2 text-left">执行器</th>
              <th className="px-4 py-2 text-left">耗时</th>
              <th className="px-4 py-2 text-left">开始时间</th>
            </tr>
          </thead>
          <tbody>
            {nodes.flatMap((n, i) => {
              const nodeId = String(n.node_id ?? '')
              const isFailed = n.status === 'failed'
              const isExpanded = expanded === nodeId
              const rows = [
                <tr key={i} className="border-t border-gray-100 hover:bg-blue-50/50">
                  <td className="px-4 py-2 font-medium text-gray-900 text-sm" onClick={() => setExpanded(isExpanded ? null : nodeId)} style={{ cursor: 'pointer' }}>
                    {isExpanded ? '▼ ' : '▶ '}{nodeId}
                  </td>
                  <td className="px-4 py-2">
                    {isFailed
                      ? <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">失败</span>
                      : <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-600">成功</span>}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">{String(n.executor_type ?? '-')}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">{formatDuration(Number(n.duration_ms ?? null))}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">{formatTime(String(n.started_at ?? ''))}</td>
                </tr>,
              ]
              if (isExpanded) {
                rows.push(
                  <tr key={`${i}-detail`}>
                    <td colSpan={5} className="px-6 py-3 bg-gray-50">
                      <pre className="text-xs overflow-x-auto">{JSON.stringify(n, null, 2)}</pre>
                    </td>
                  </tr>,
                )
              }
              return rows
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function EventTimeline({ data }: { data: RunArchiveData }) {
  const events = data.flowEvents as Array<Record<string, unknown>>
  const [filter, setFilter] = useState<'all' | 'error'>('all')

  const filtered = useMemo(() => {
    if (filter === 'error') {
      return events.filter(e => {
        const type = String(e.event_type ?? '')
        return type.includes('failed') || e.error_text
      })
    }
    return events
  }, [events, filter])

  return (
    <div>
      <div className="flex gap-2 mb-3">
        <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} label={`全部(${events.length})`} />
        <FilterChip active={filter === 'error'} onClick={() => setFilter('error')} label="错误事件" />
      </div>
      <div className="space-y-1 max-h-96 overflow-y-auto">
        {filtered.map((e, i) => {
          const type = String(e.event_type ?? '')
          const isError = type.includes('failed') || e.error_text
          const color = isError ? 'bg-red-500' : type.includes('succeeded') ? 'bg-emerald-500' : type.includes('started') ? 'bg-blue-500' : 'bg-gray-300'
          return (
            <div key={i} className="flex items-center gap-3 text-xs">
              <span className="text-gray-400 w-20">{formatTime(String(e.time ?? ''))}</span>
              <span className={`w-2 h-2 rounded-full ${color}`} />
              <span className="text-gray-700">{type}</span>
              {e.error_text && <span className="text-red-600 truncate">{String(e.error_text).slice(0, 100)}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RunLogsPanel({ data }: { data: RunArchiveData }) {
  const [levelFilter, setLevelFilter] = useState<'all' | 'error' | 'warn'>('all')
  const logs = data.runLogs
  const filtered = useMemo(() => {
    if (levelFilter === 'all') return logs
    return logs.filter(l => levelFilter === 'error' ? l.level === 'error' : l.level === 'error' || l.level === 'warn')
  }, [logs, levelFilter])

  return (
    <div>
      <div className="flex gap-2 mb-3">
        <FilterChip active={levelFilter === 'all'} onClick={() => setLevelFilter('all')} label={`全部(${logs.length})`} />
        <FilterChip active={levelFilter === 'error'} onClick={() => setLevelFilter('error')} label="ERROR" />
        <FilterChip active={levelFilter === 'warn'} onClick={() => setLevelFilter('warn')} label="WARN+" />
      </div>
      <div className="bg-gray-900 text-gray-100 rounded-lg p-3 font-mono text-xs max-h-96 overflow-y-auto">
        {filtered.map((l, i) => (
          <div key={i} className={l.level === 'error' ? 'text-red-400' : l.level === 'warn' ? 'text-amber-400' : 'text-gray-300'}>
            {formatTime(new Date(l.timestamp).toISOString())} [{l.level.toUpperCase().padEnd(5)}] {l.source ?? ''} {l.message}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Part 2: Error Details ──

function ErrorDetailsPanel({ data }: { data: RunArchiveData }) {
  const failedNodes = data.failureSummary.failedNodes
  return (
    <div className="space-y-4">
      {failedNodes.map((fn, i) => (
        <div key={i} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            <span className="font-medium text-gray-900 text-sm">{fn.nodeId}</span>
            <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">失败</span>
            <span className="text-xs text-gray-400">{fn.executorType ?? '-'}</span>
            <span className="text-xs text-gray-400">重试: {fn.attempt}</span>
          </div>

          <div className="bg-red-50 border border-red-100 rounded-lg p-3 text-sm text-red-800 mb-3">
            <span className="font-medium">错误: </span>
            {fn.error ?? '(无错误信息)'}
          </div>

          {data.failureSummary.rootCauseHints.length > 0 && (
            <div className="text-xs text-gray-600 mb-3">
              <span className="font-medium">根因提示: </span>
              {data.failureSummary.rootCauseHints.filter(h => h.includes(fn.nodeId)).join('; ') || '-'}
            </div>
          )}

          {data.failureSummary.errorTimeline.length > 0 && (
            <Collapsible title="错误时间线">
              <div className="space-y-1">
                {data.failureSummary.errorTimeline
                  .filter(e => e.detail.includes(fn.nodeId) || e.event.includes(fn.nodeId))
                  .map((e, j) => (
                    <div key={j} className="flex items-center gap-2 text-xs text-gray-600">
                      <span className="text-gray-400 w-20">{formatTime(e.timestamp)}</span>
                      <span>{e.event}</span>
                      <span className="text-red-500 truncate">{e.detail}</span>
                    </div>
                  ))}
              </div>
            </Collapsible>
          )}

          <Collapsible title="Input">
            <pre className="bg-gray-50 rounded p-2 text-xs overflow-x-auto max-h-40">
              {(() => {
                const ne = (data.nodeExecutions as Array<Record<string, unknown>>)
                  .find(n => String(n.node_id) === fn.nodeId)
                return ne?.input_json ? JSON.stringify(JSON.parse(String(ne.input_json)), null, 2) : '(无)'
              })()}
            </pre>
          </Collapsible>

          <Collapsible title="Output">
            <pre className="bg-gray-50 rounded p-2 text-xs overflow-x-auto max-h-40">
              {(() => {
                const ne = (data.nodeExecutions as Array<Record<string, unknown>>)
                  .find(n => String(n.node_id) === fn.nodeId)
                return ne?.output_json ? JSON.stringify(JSON.parse(String(ne.output_json)), null, 2) : '(无输出)'
              })()}
            </pre>
          </Collapsible>

          <Collapsible title="Session 错误">
            <div className="text-xs text-gray-600">
              {fn.embeddedSessionKey
                ? `Session Key: ${fn.embeddedSessionKey}`
                : '(无 embedded session)'}
            </div>
          </Collapsible>
        </div>
      ))}
    </div>
  )
}

// ── Part 3: AI Diagnosis ──

function AnalysisPanel({ analysis }: { analysis: NonNullable<RunArchiveData['analysis']> }) {
  const sourceCfg = ANALYSIS_SOURCE_LABEL[analysis.source] ?? ANALYSIS_SOURCE_LABEL.pending
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 text-sm">
        <span className={`rounded-full px-2.5 py-0.5 text-xs ${sourceCfg.cls}`}>{sourceCfg.label}</span>
        <span className="text-gray-500">诊断数: {analysis.diagnoses.length}</span>
        {analysis.error && <span className="text-red-600 text-xs">{analysis.error}</span>}
      </div>

      {analysis.diagnoses.length === 0 && (
        <div className="text-sm text-gray-500 bg-gray-50 rounded-lg p-4">
          {analysis.source === 'failed' ? '分析不可用，请尝试手动分析' : '暂无诊断结果'}
        </div>
      )}

      {analysis.diagnoses.map((d: RunArchiveDiagnosis, i) => (
        <div key={i} className="rounded-xl border-l-4 border-l-amber-400 border border-slate-200 bg-white p-5">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-xs text-gray-400">诊断 #{i + 1}</span>
            {d.nodeId && <span className="text-sm font-medium text-gray-900">{d.nodeId}</span>}
            <span className={`rounded-full px-2 py-0.5 text-xs ${SEVERITY_COLORS[d.severity] ?? 'bg-gray-100 text-gray-600'}`}>
              {d.severity}
            </span>
            <span className="text-xs text-gray-500">{d.failureMode}</span>
          </div>

          <div className="mb-3">
            <div className="text-xs font-medium text-gray-500 mb-1">根因分析</div>
            <p className="text-sm text-gray-800">{d.reasoning}</p>
          </div>

          {d.evidenceEventIds.length > 0 && (
            <div className="mb-3">
              <div className="text-xs font-medium text-gray-500 mb-1">证据</div>
              <ul className="text-xs text-gray-600 list-disc list-inside">
                {d.evidenceEventIds.map((e, j) => <li key={j}>{e}</li>)}
              </ul>
            </div>
          )}

          {d.suggestedFixSpec && (
            <div className="mb-3">
              <div className="text-xs font-medium text-gray-500 mb-1">建议</div>
              <p className="text-sm text-gray-800">{d.suggestedFixSpec}</p>
            </div>
          )}

          {d.proposal && d.proposal.operations.length > 0 && (
            <Collapsible title="建议修复 (workflow-patch)" badge={<span className="text-xs text-blue-500">{d.proposal.operations.length} ops</span>}>
              <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs overflow-x-auto">
                {JSON.stringify(d.proposal.operations, null, 2)}
              </pre>
            </Collapsible>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Part 4: Suggested YAML ──

function SuggestedYamlPanel({ yaml }: { yaml: NonNullable<RunArchiveData['suggestedYaml']> }) {
  const [view, setView] = useState<'suggested' | 'diff'>('suggested')
  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <span className={`rounded-full px-2.5 py-0.5 text-xs ${SEVERITY_COLORS[yaml.confidence] ?? 'bg-gray-100'}`}>
          置信度: {yaml.confidence}
        </span>
        <span className="text-sm text-gray-600">{yaml.summary}</span>
        <div className="flex gap-2 ml-auto">
          <FilterChip active={view === 'suggested'} onClick={() => setView('suggested')} label="建议 YAML" />
          <FilterChip active={view === 'diff'} onClick={() => setView('diff')} label="Diff" />
        </div>
      </div>

      {view === 'suggested' ? (
        <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 font-mono text-xs overflow-x-auto max-h-[600px]">
          {yaml.yamlContent}
        </pre>
      ) : (
        <div>
          {yaml.patchProposal && (
            <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 font-mono text-xs overflow-x-auto max-h-[600px]">
              {JSON.stringify(yaml.patchProposal, null, 2)}
            </pre>
          )}
        </div>
      )}

      <div className="flex gap-3 mt-4">
        <button
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
          onClick={() => navigator.clipboard.writeText(yaml.yamlContent)}
        >
          复制 YAML
        </button>
        <button
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
          onClick={() => {
            const blob = new Blob([yaml.yamlContent], { type: 'text/yaml' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'suggested-workflow.yaml'
            a.click()
            URL.revokeObjectURL(url)
          }}
        >
          下载 YAML
        </button>
      </div>
    </div>
  )
}

// ── Small helpers ──

function DefItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className={`text-sm text-gray-900 ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  )
}

function FilterChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      className={`rounded-full px-3 py-0.5 text-xs ${active ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

// ── Main Panel ──

type SectionId = 'overview' | 'process' | 'errors' | 'analysis' | 'yaml'

export default function RunArchivePanel({
  flowId,
  runStatus,
}: {
  flowId: string
  runStatus?: string | null
}) {
  const [section, setSection] = useState<SectionId>('overview')
  const { data, isLoading, isError, error } = useRunArchive(flowId)

  // Only show for failed runs
  const isFailed = runStatus === 'failed' || runStatus === 'cancelled' || runStatus === 'timeout'
  if (!isFailed) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
        <p className="text-sm text-gray-500">运行档案仅对失败的工作流生成。本次运行状态: {runStatus ?? 'unknown'}</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="h-8 bg-gray-100 rounded animate-pulse" />
        <div className="h-32 bg-gray-100 rounded animate-pulse" />
        <div className="h-32 bg-gray-100 rounded animate-pulse" />
        <p className="text-sm text-gray-500 text-center">正在生成运行档案...</p>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4">
        <p className="text-sm text-red-700">加载运行档案失败: {error?.message ?? '未知错误'}</p>
      </div>
    )
  }

  const navItems: Array<{ id: SectionId; label: string; sub?: string[] }> = [
    { id: 'overview', label: '运行概览' },
    {
      id: 'process', label: '运行过程', sub: ['流程信息', '节点执行', '事件流', '步骤追踪', '运行日志'],
    },
    { id: 'errors', label: '错误详情' },
    { id: 'analysis', label: 'AI 诊断' },
    { id: 'yaml', label: '建议 YAML' },
  ]

  return (
    <div className="flex gap-4">
      {/* Left Nav */}
      <nav className="w-48 shrink-0">
        <div className="space-y-1">
          {navItems.map(item => (
            <div key={item.id}>
              <button
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm rounded-lg ${section === item.id ? 'bg-blue-50 text-blue-600 font-medium' : 'text-gray-600 hover:bg-gray-50'}`}
                onClick={() => setSection(item.id)}
              >
                {item.label}
              </button>
            </div>
          ))}
        </div>
      </nav>

      {/* Right Content */}
      <div className="flex-1 min-w-0">
        {section === 'overview' && <OverviewSection data={data} />}
        {section === 'process' && <ProcessSection data={data} />}
        {section === 'errors' && <ErrorDetailsPanel data={data} />}
        {section === 'analysis' && (
          data.analysis ? <AnalysisPanel analysis={data.analysis} /> : (
            <div className="text-sm text-gray-500 bg-gray-50 rounded-lg p-4">暂无 AI 诊断数据</div>
          )
        )}
        {section === 'yaml' && (
          data.suggestedYaml ? <SuggestedYamlPanel yaml={data.suggestedYaml} /> : (
            <div className="text-sm text-gray-500 bg-gray-50 rounded-lg p-4">暂无建议 YAML</div>
          )
        )}
      </div>

      {/* Bottom Status Bar */}
      <div className="border-t border-slate-200 bg-white px-4 py-2 text-xs text-gray-500 flex items-center gap-4 mt-4">
        <span>archive_id: <span className="font-mono">{data.archive.archiveId}</span></span>
        {data.analysis && (
          <span>source: <span className={data.analysis.source === 'reused' ? 'text-emerald-600' : data.analysis.source === 'preview' ? 'text-blue-600' : 'text-red-600'}>{data.analysis.source}</span></span>
        )}
        <span>generated: {formatTime(data.archive.createdAt)}</span>
      </div>
    </div>
  )
}

// ── Overview Section ──

function OverviewSection({ data }: { data: RunArchiveData }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="档案状态" value={data.archive.status} color="emerald" />
        <StatCard label="分析来源" value={data.analysis?.source ?? '-'} color="blue" />
        <StatCard label="诊断数量" value={String(data.analysis?.diagnoses.length ?? 0)} color="amber" />
        <StatCard label="失败节点" value={String(data.failureSummary.failedNodeCount)} color="red" />
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-medium text-gray-900 mb-3">失败节点摘要</h3>
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500">
            <tr>
              <th className="px-3 py-2 text-left">节点</th>
              <th className="px-3 py-2 text-left">状态</th>
              <th className="px-3 py-2 text-left">错误</th>
            </tr>
          </thead>
          <tbody>
            {data.failureSummary.failedNodes.map((fn, i) => (
              <tr key={i} className="border-t border-gray-100">
                <td className="px-3 py-2 font-medium text-gray-900 text-sm">{fn.nodeId}</td>
                <td className="px-3 py-2"><span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">失败</span></td>
                <td className="px-3 py-2 text-xs text-gray-600 truncate max-w-xs">{fn.error ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.analysis && data.analysis.diagnoses.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-medium text-gray-900 mb-3">AI 诊断摘要</h3>
          <div className="space-y-3">
            {data.analysis.diagnoses.map((d, i) => (
              <div key={i} className="text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs text-gray-400">#{i + 1}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${SEVERITY_COLORS[d.severity] ?? ''}`}>{d.failureMode}</span>
                </div>
                <p className="text-gray-700 text-xs">{d.reasoning.slice(0, 200)}</p>
                {d.suggestedFixSpec && <p className="text-blue-600 text-xs mt-1">建议: {d.suggestedFixSpec.slice(0, 150)}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {data.suggestedYaml && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-medium text-gray-900 mb-2">建议 YAML 预览</h3>
          <p className="text-xs text-gray-600 mb-2">{data.suggestedYaml.summary}</p>
          <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-xs overflow-x-auto max-h-48">
            {data.suggestedYaml.yamlContent.slice(0, 1000)}
            {data.suggestedYaml.yamlContent.length > 1000 ? '\n...' : ''}
          </pre>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  const colorMap: Record<string, string> = {
    emerald: 'border-l-emerald-400',
    blue: 'border-l-blue-400',
    amber: 'border-l-amber-400',
    red: 'border-l-red-400',
  }
  return (
    <div className={`rounded-xl border border-slate-200 border-l-4 ${colorMap[color] ?? ''} bg-white p-4`}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-gray-900 mt-1">{value}</div>
    </div>
  )
}

// ── Process Section (tabbed) ──

function ProcessSection({ data }: { data: RunArchiveData }) {
  const [sub, setSub] = useState<'info' | 'nodes' | 'events' | 'logs'>('info')
  return (
    <div>
      <div className="flex gap-2 mb-4">
        <FilterChip active={sub === 'info'} onClick={() => setSub('info')} label="流程信息" />
        <FilterChip active={sub === 'nodes'} onClick={() => setSub('nodes')} label="节点执行" />
        <FilterChip active={sub === 'events'} onClick={() => setSub('events')} label="事件流" />
        <FilterChip active={sub === 'logs'} onClick={() => setSub('logs')} label="运行日志" />
      </div>
      {sub === 'info' && <ProcessInfoPanel data={data} />}
      {sub === 'nodes' && <NodeExecTable data={data} />}
      {sub === 'events' && <EventTimeline data={data} />}
      {sub === 'logs' && <RunLogsPanel data={data} />}
    </div>
  )
}
