import { useParams, Link } from 'react-router-dom'
import { useBenchRun, useBenchResults, useBenchSessionByArtifact, useBenchSessions } from '../api/hooks'
import { Fragment, useState } from 'react'
import { benchStatusLabel, benchText } from '../bench/i18n'
import { BenchEmptyState, BenchErrorState, BenchLoadingState } from '../bench/ui-state'
import { formatTokenUsage, tokenBreakdownText, tokenValue } from '../bench/token'
import { eventContentBlocks, eventLabel, eventRole, eventUsageText, type BenchSessionEvent } from '../bench/session'

export default function BenchRunDetail({ basePath = '/bench' }: { basePath?: string }) {
  const { benchRunId } = useParams<{ benchRunId: string }>()
  const { data: run, isLoading: runLoading } = useBenchRun(benchRunId ?? '')
  const { data: results, isLoading: resultsLoading } = useBenchResults(benchRunId ?? '')
  const { data: sessionsData, isLoading: sessionsLoading, isError: sessionsError } = useBenchSessions(benchRunId ?? '')
  const [expandedResultId, setExpandedResultId] = useState<string | null>(null)
  const [selectedSessionArtifactId, setSelectedSessionArtifactId] = useState<string | null>(null)
  const { data: selectedSession, isLoading: sessionLoading } = useBenchSessionByArtifact(benchRunId ?? '', selectedSessionArtifactId)

  const toDisplayText = (value: unknown, fallback = '-'): string => {
    if (value === null || value === undefined || value === '') return fallback
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }

  const toNumberOrNull = (value: unknown): number | null => {
    if (value === null || value === undefined || value === '') return null
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }

  const statusBadge = (statusValue: unknown) => {
    const status = toDisplayText(statusValue, 'unknown')
    const map: Record<string, string> = {
      pending: 'bg-gray-100 text-gray-700',
      running: 'bg-blue-100 text-blue-700',
      succeeded: 'bg-green-100 text-green-700',
      failed: 'bg-red-100 text-red-700',
      timeout: 'bg-yellow-100 text-yellow-700',
      cancelled: 'bg-orange-100 text-orange-700',
    }
    return (
      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-700'}`}>
        {benchStatusLabel(status)}
      </span>
    )
  }

  const runnerWarningBadge = () => (
    <span className="inline-flex rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">
      运行器异常
    </span>
  )

  const scorePercent = (score: unknown, maxScore?: unknown) => {
    const s = Number(score)
    const m = maxScore !== undefined ? Number(maxScore) : null
    if (Number.isNaN(s)) return '-'
    const ratio = m !== null && !Number.isNaN(m) && m > 0 ? s / m : s
    const pct = (ratio * 100).toFixed(1) + '%'
    if (m !== null && !Number.isNaN(m) && m > 0) {
      return `${pct} (${s} / ${m})`
    }
    return pct
  }

  const scorePercentOnly = (score: unknown, maxScore?: unknown) => {
    const s = Number(score)
    const m = maxScore !== undefined ? Number(maxScore) : null
    if (Number.isNaN(s)) return '-'
    const ratio = m !== null && !Number.isNaN(m) && m > 0 ? s / m : s
    return `${(ratio * 100).toFixed(1)}%`
  }

  const formatPercent = (value: unknown) => {
    const n = Number(value)
    if (Number.isNaN(n)) return '-'
    return `${(n * 100).toFixed(1)}%`
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null || seconds === undefined) return '-'
    if (seconds < 60) return `${seconds}s`
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins < 60) return `${mins}m ${secs}s`
    const hrs = Math.floor(mins / 60)
    const remainingMins = mins % 60
    return `${hrs}h ${remainingMins}m ${secs}s`
  }

  const MetricTile = ({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'good' | 'warn' | 'bad' }) => {
    const toneClass = {
      default: 'border-gray-200 bg-white text-gray-900',
      good: 'border-green-200 bg-green-50 text-green-900',
      warn: 'border-yellow-200 bg-yellow-50 text-yellow-900',
      bad: 'border-red-200 bg-red-50 text-red-900',
    }[tone]
    return (
      <div className={`rounded-md border px-3 py-2 ${toneClass}`}>
        <div className="text-xs text-gray-500">{label}</div>
        <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      </div>
    )
  }

  const OverviewItem = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="min-w-0 rounded-md border border-gray-100 bg-gray-50 px-3 py-2.5">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1 min-w-0 break-words text-sm leading-5 text-gray-900">{children}</div>
    </div>
  )

  const DetailItem = ({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) => (
    <div className="min-w-0 rounded-md bg-gray-50 px-3 py-2">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 min-w-0 break-words text-sm text-gray-900 ${mono ? 'font-mono text-xs leading-5' : ''}`}>{children}</div>
    </div>
  )

  const MarkdownReport = ({ markdown }: { markdown: string }) => {
    const lines = markdown.split(/\r?\n/)
    const blocks: React.ReactNode[] = []
    let i = 0

    const inline = (text: string) => {
      const nodes: React.ReactNode[] = []
      const re = /(`[^`]+`|\*\*[^*]+\*\*)/g
      let last = 0
      for (const match of text.matchAll(re)) {
        if (match.index > last) nodes.push(text.slice(last, match.index))
        const token = match[0]
        if (token.startsWith('`')) {
          nodes.push(<code key={`${match.index}-code`} className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.85em] text-gray-900">{token.slice(1, -1)}</code>)
        } else {
          nodes.push(<strong key={`${match.index}-strong`} className="font-semibold text-gray-950">{token.slice(2, -2)}</strong>)
        }
        last = match.index + token.length
      }
      if (last < text.length) nodes.push(text.slice(last))
      return nodes
    }

    const isTableSeparator = (line: string) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line)
    const splitTableRow = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())

    while (i < lines.length) {
      const line = lines[i]
      const key = blocks.length

      if (!line.trim()) {
        i += 1
        continue
      }

      if (line.startsWith('```')) {
        const codeLines: string[] = []
        i += 1
        while (i < lines.length && !lines[i].startsWith('```')) {
          codeLines.push(lines[i])
          i += 1
        }
        i += 1
        blocks.push(
          <pre key={key} className="my-3 overflow-auto rounded-md bg-gray-950 p-3 text-xs text-gray-100">
            <code>{codeLines.join('\n')}</code>
          </pre>
        )
        continue
      }

      const heading = /^(#{1,4})\s+(.+)$/.exec(line)
      if (heading) {
        const level = heading[1].length
        const text = heading[2]
        const className = level === 1
          ? 'mt-5 mb-3 text-xl font-semibold text-gray-950'
          : level === 2
          ? 'mt-5 mb-2 text-base font-semibold text-gray-950'
          : 'mt-4 mb-2 text-sm font-semibold text-gray-900'
        const Tag = (`h${Math.min(level + 1, 4)}`) as keyof JSX.IntrinsicElements
        blocks.push(<Tag key={key} className={className}>{inline(text)}</Tag>)
        i += 1
        continue
      }

      if (line.trim() === '---') {
        blocks.push(<hr key={key} className="my-4 border-gray-200" />)
        i += 1
        continue
      }

      if (line.trim().startsWith('>')) {
        const quoteLines: string[] = []
        while (i < lines.length && lines[i].trim().startsWith('>')) {
          quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
          i += 1
        }
        blocks.push(
          <blockquote key={key} className="my-3 border-l-4 border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
            {quoteLines.map((item, idx) => <div key={idx}>{inline(item)}</div>)}
          </blockquote>
        )
        continue
      }

      if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        const header = splitTableRow(line)
        const rows: string[][] = []
        i += 2
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
          rows.push(splitTableRow(lines[i]))
          i += 1
        }
        blocks.push(
          <div key={key} className="my-3 overflow-x-auto rounded-md border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>{header.map((cell, idx) => <th key={idx} className="px-3 py-2 text-left text-xs font-medium text-gray-600">{inline(cell)}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row.map((cell, cellIdx) => <td key={cellIdx} className="px-3 py-2 text-gray-800">{inline(cell)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
        continue
      }

      if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
        const ordered = /^\s*\d+\.\s+/.test(line)
        const items: string[] = []
        const itemRe = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/
        while (i < lines.length && itemRe.test(lines[i])) {
          items.push(lines[i].replace(itemRe, ''))
          i += 1
        }
        const ListTag = ordered ? 'ol' : 'ul'
        blocks.push(
          <ListTag key={key} className={`${ordered ? 'list-decimal' : 'list-disc'} my-2 pl-5 text-sm text-gray-800 space-y-1`}>
            {items.map((item, idx) => <li key={idx}>{inline(item)}</li>)}
          </ListTag>
        )
        continue
      }

      const paragraph: string[] = [line]
      i += 1
      while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s+/.test(lines[i]) && !lines[i].startsWith('```') && lines[i].trim() !== '---') {
        if (lines[i].includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) break
        if (/^\s*[-*]\s+/.test(lines[i]) || /^\s*\d+\.\s+/.test(lines[i]) || lines[i].trim().startsWith('>')) break
        paragraph.push(lines[i])
        i += 1
      }
      blocks.push(<p key={key} className="my-2 text-sm leading-6 text-gray-800">{inline(paragraph.join(' '))}</p>)
    }

    return <div className="space-y-1">{blocks.map((block, idx) => <Fragment key={idx}>{block}</Fragment>)}</div>
  }

  const JsonBlock = ({ label, data }: { label: string; data: unknown }) => {
    const [open, setOpen] = useState(false)
    if (!data) return null
    return (
      <div className="mt-2">
        <button
          onClick={() => setOpen(!open)}
          className="text-xs text-blue-600 hover:underline"
        >
          {open ? '收起' : '查看'} {label}
        </button>
        {open && (
          <pre className="mt-1 rounded-md bg-gray-50 p-2 text-xs text-gray-800 overflow-auto max-h-64">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
      </div>
    )
  }

  if (runLoading) {
    return <div className="p-6"><BenchLoadingState message="正在加载 Bench Run..." /></div>
  }

  if (!run) {
    return <div className="p-6"><BenchErrorState message="Bench Run 不存在" /></div>
  }

  const runScore = toNumberOrNull(run.score)
  const runMaxScore = toNumberOrNull(run.maxScore)
  const runPassRate = toNumberOrNull(run.passRate)
  const runStartedAt = toNumberOrNull(run.startedAt)
  const runCompletedAt = toNumberOrNull(run.completedAt)
  const duration = runStartedAt && runCompletedAt
    ? runCompletedAt - runStartedAt
    : null

  const taskCount = run.summary?.taskCount ?? results?.length ?? null
  const succeededCount = run.summary?.succeededCount ?? (results ? results.filter((r) => r.status === 'succeeded').length : null)
  const failedCount = run.summary?.failedCount ?? (results ? results.filter((r) => r.status === 'failed').length : null)
  const isDomainRun = run.runScope === 'domain'
  const runSummary = run.summary && typeof run.summary === 'object' ? run.summary as Record<string, unknown> : null
  const runnerStatus = toDisplayText(runSummary?.runnerStatus, '')
  const hasRunnerWarning = !!runnerStatus && runnerStatus !== 'succeeded'
  const runnerWarning = toDisplayText(runSummary?.runnerWarning, '')
  const runnerError = toDisplayText(runSummary?.runnerError, '')
  const progress = runSummary?.progress && typeof runSummary.progress === 'object'
    ? runSummary.progress as Record<string, unknown>
    : null
  const progressCompleted = toNumberOrNull(progress?.taskCompleted)
  const progressTotal = toNumberOrNull(progress?.taskTotal)
  const progressPercent = progressCompleted !== null && progressTotal !== null && progressTotal > 0
    ? Math.min(100, Math.max(0, (progressCompleted / progressTotal) * 100))
    : null
  const progressUpdatedAt = toNumberOrNull(progress?.lastUpdatedAt)
  const progressTokenUsage = progress?.tokenUsageSoFar && typeof progress.tokenUsageSoFar === 'object'
    ? progress.tokenUsageSoFar as Record<string, unknown>
    : null
  const progressTokenUsageForDisplay = progressTokenUsage ? {
    inputTokens: toNumberOrNull(progressTokenUsage.inputTokens) ?? undefined,
    outputTokens: toNumberOrNull(progressTokenUsage.outputTokens) ?? undefined,
    cacheReadTokens: toNumberOrNull(progressTokenUsage.cacheReadTokens) ?? undefined,
    cacheWriteTokens: toNumberOrNull(progressTokenUsage.cacheWriteTokens) ?? undefined,
    totalTokens: toNumberOrNull(progressTokenUsage.totalTokens) ?? undefined,
  } : null
  const metricTokenUsage = run.tokenUsage ?? progressTokenUsageForDisplay

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center gap-2 text-sm text-gray-500 mb-2">
          <Link to={`${basePath}/domains`} className="hover:text-blue-600">← {benchText.domains}</Link>
          <span>/</span>
          <Link to={`${basePath}/domains/${encodeURIComponent(run.ownerUserId)}/${encodeURIComponent(run.domainId)}`} className="hover:text-blue-600">{toDisplayText(run.ownerUserId)}/{toDisplayText(run.domainId)}</Link>
          <span>/</span>
          {isDomainRun ? (
            <span className="text-gray-700">Domain Run（{toDisplayText(run.templateCount, '?')} 个模板）</span>
          ) : (
            <Link to={`${basePath}/domains/${encodeURIComponent(run.ownerUserId)}/${encodeURIComponent(run.domainId)}/templates/${encodeURIComponent(run.templateName)}`} className="hover:text-blue-600">
              {toDisplayText(run.templateName)} v{toDisplayText(run.templateVersion)}
            </Link>
          )}
          <span>/</span>
          <span className="text-gray-900 font-medium">{benchText.runs}</span>
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-gray-900 font-mono text-sm">{toDisplayText(run.benchRunId)}</h1>
            {statusBadge(run.status)}
            {hasRunnerWarning && runnerWarningBadge()}
            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${isDomainRun ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-700'}`}>
              {toDisplayText(run.runScope, 'template')}
            </span>
          </div>
          {runScore !== null && (
            <div className="text-right">
              <div className="text-2xl font-bold text-gray-900">{scorePercentOnly(runScore, runMaxScore ?? undefined)}</div>
              {runPassRate !== null && (
                <div className="text-xs text-gray-500">{benchText.passRate}: {(runPassRate * 100).toFixed(1)}%</div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-4 space-y-6">
          {/* Run Overview */}
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">{benchText.overview}</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <OverviewItem label={benchText.domain}>
                <span className="font-mono text-xs">{toDisplayText(run.domainId)}</span>
              </OverviewItem>
              {isDomainRun ? (
                <>
                  <OverviewItem label="运行范围">
                    Domain Run（{toDisplayText(run.templateCount, '?')} 个模板）
                  </OverviewItem>
                  <OverviewItem label={benchText.model}>
                    <span className="font-mono text-xs">{toDisplayText(run.model)}</span>
                  </OverviewItem>
                  <OverviewItem label={benchText.suite}>{toDisplayText(run.suite)}</OverviewItem>
                </>
              ) : (
                <>
                  <OverviewItem label={benchText.template}>
                    <Link
                      to={`${basePath}/domains/${encodeURIComponent(run.ownerUserId)}/${encodeURIComponent(run.domainId)}/templates/${encodeURIComponent(run.templateName)}`}
                      className="font-mono text-xs text-blue-600 hover:underline"
                    >
                      {toDisplayText(run.templateName)} v{toDisplayText(run.templateVersion)}
                    </Link>
                  </OverviewItem>
                  <OverviewItem label={benchText.model}>
                    <span className="font-mono text-xs">{toDisplayText(run.model)}</span>
                  </OverviewItem>
                  <OverviewItem label={benchText.suite}>{toDisplayText(run.suite)}</OverviewItem>
                </>
              )}
              <OverviewItem label={benchText.scene}>
                <span className="font-mono text-xs">{toDisplayText(run.scene)}</span>
              </OverviewItem>
              <OverviewItem label={benchText.status}>
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  {statusBadge(run.status)}
                  {hasRunnerWarning && runnerWarningBadge()}
                </span>
              </OverviewItem>
              <OverviewItem label={benchText.started}>
                <span className="font-mono text-xs">{runStartedAt ? new Date(runStartedAt * 1000).toLocaleString() : '-'}</span>
              </OverviewItem>
              <OverviewItem label={benchText.completed}>
                <span className="font-mono text-xs">{runCompletedAt ? new Date(runCompletedAt * 1000).toLocaleString() : '-'}</span>
              </OverviewItem>
            </div>

            {run.clawmindFlowId ? (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="text-xs text-gray-500 mb-1">ClawMind Flow</div>
                <div className="text-sm">
                  <Link to={`/runs/${run.clawmindFlowId}`} className="text-blue-600 hover:underline font-mono text-xs">
                    {toDisplayText(run.clawmindFlowId)}
                  </Link>
                </div>
              </div>
            ) : (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="text-xs text-gray-500 mb-1">ClawMind Flow</div>
                <div className="text-sm text-gray-400">-</div>
              </div>
            )}

            {hasRunnerWarning && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="text-xs text-yellow-700 mb-1">运行器异常</div>
                <div className="rounded-md bg-yellow-50 p-2 text-sm text-yellow-800">
                  <div>Runner 状态：<span className="font-mono text-xs">{runnerStatus}</span></div>
                  {runnerWarning && <div className="mt-1 whitespace-pre-wrap">{runnerWarning}</div>}
                  {runnerError && <div className="mt-1 whitespace-pre-wrap">{runnerError}</div>}
                </div>
              </div>
            )}

            {run.errorText && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <div className="text-xs text-red-500 mb-1">错误</div>
                <div className="rounded-md bg-red-50 p-2 text-sm text-red-700 whitespace-pre-wrap">{toDisplayText(run.errorText)}</div>
              </div>
            )}
          </section>

          {/* Metrics */}
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">{benchText.metrics}</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <MetricTile
                label={benchText.score}
                value={runScore !== null && runMaxScore !== null ? `${runScore.toFixed(3)} / ${runMaxScore.toFixed(3)}` : '-'}
                tone={run.status === 'succeeded' ? 'good' : run.status === 'failed' ? 'bad' : 'default'}
              />
              <MetricTile label={benchText.maxScore} value={runMaxScore !== null ? runMaxScore.toFixed(3) : '-'} />
              <MetricTile label={benchText.passRate} value={runPassRate !== null ? formatPercent(runPassRate) : '-'} />
              <MetricTile label="任务数" value={taskCount !== null ? String(taskCount) : '-'} />
              <MetricTile label="成功任务" value={succeededCount !== null ? String(succeededCount) : '-'} tone={Number(succeededCount ?? 0) > 0 ? 'good' : 'default'} />
              <MetricTile label="失败任务" value={failedCount !== null ? String(failedCount) : '-'} tone={Number(failedCount ?? 0) > 0 ? 'warn' : 'good'} />
              <MetricTile label={benchText.duration} value={formatDuration(duration)} />
              <MetricTile label={benchText.totalToken} value={formatTokenUsage(metricTokenUsage)} />
              <MetricTile label={benchText.inputToken} value={tokenValue(metricTokenUsage, 'inputTokens')} />
              <MetricTile label={benchText.outputToken} value={tokenValue(metricTokenUsage, 'outputTokens')} />
              <MetricTile label={benchText.cacheReadToken} value={tokenValue(metricTokenUsage, 'cacheReadTokens')} />
            </div>
          </section>

          {progress ? (
            <section className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-gray-900">运行进度</h2>
                <span className="text-xs text-gray-500">
                  {progressUpdatedAt ? new Date(progressUpdatedAt * 1000).toLocaleString() : '等待更新'}
                </span>
              </div>
              <div className="mb-4">
                <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
                  <span>{toDisplayText(progress.phase, 'running')}</span>
                  <span>
                    {progressCompleted !== null && progressTotal !== null
                      ? `${progressCompleted} / ${progressTotal}`
                      : '-'}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all"
                    style={{ width: `${progressPercent ?? 0}%` }}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                <div>
                  <div className="text-xs text-gray-500">最近任务</div>
                  <div className="mt-1 truncate font-mono text-xs text-gray-900">{toDisplayText(progress.lastTaskId ?? progress.currentTaskId)}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">成功 / 失败</div>
                  <div className="mt-1 text-gray-900">
                    {toDisplayText(progress.taskSucceeded, '0')} / {toDisplayText(progress.taskFailed, '0')}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">当前得分</div>
                  <div className="mt-1 text-gray-900">
                    {toNumberOrNull(progress.scoreSoFar) !== null && toNumberOrNull(progress.maxScoreSoFar) !== null
                      ? `${toNumberOrNull(progress.scoreSoFar)!.toFixed(3)} / ${toNumberOrNull(progress.maxScoreSoFar)!.toFixed(3)}`
                      : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">当前 Token</div>
                  <div className="mt-1 text-gray-900">
                    {progressTokenUsage ? formatTokenUsage({
                      inputTokens: toNumberOrNull(progressTokenUsage.inputTokens),
                      outputTokens: toNumberOrNull(progressTokenUsage.outputTokens),
                      totalTokens: toNumberOrNull(progressTokenUsage.totalTokens),
                    }) : '-'}
                  </div>
                </div>
              </div>
            </section>
          ) : run.status === 'running' ? (
            <section className="rounded-lg border border-gray-200 bg-white p-4">
              <h2 className="text-sm font-semibold text-gray-900 mb-2">运行进度</h2>
              <div className="text-sm text-gray-500">等待 ClawBench 回传进度</div>
            </section>
          ) : null}

          {/* Task Results */}
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">{benchText.taskResults}</h2>
            {resultsLoading ? (
              <BenchLoadingState message="正在加载任务结果..." />
            ) : !results || results.length === 0 ? (
              <BenchEmptyState message={benchText.noResults} />
            ) : (
              <div className="space-y-3">
                {results.map((r) => (
                  <div
                    key={r.resultId}
                    className="rounded-md border border-gray-200 bg-white"
                  >
                    <div
                      className="grid cursor-pointer gap-3 px-4 py-3 hover:bg-gray-50 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center"
                      onClick={() => setExpandedResultId(expandedResultId === r.resultId ? null : r.resultId)}
                    >
                      <div className="min-w-0">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="min-w-0 break-all font-mono text-xs font-medium text-gray-900">{toDisplayText(r.taskId)}</span>
                          {statusBadge(r.status)}
                        </div>
                        {r.taskName && r.taskName !== r.taskId && (
                          <div className="mt-1 break-words text-sm text-gray-700">{toDisplayText(r.taskName)}</div>
                        )}
                        {r.resultJson?.templateName && (
                          <div className="mt-2 inline-flex max-w-full rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
                            <span className="min-w-0 break-all font-mono">
                              {String(r.resultJson.templateName)}
                              {r.resultJson.templateVersion !== undefined && r.resultJson.templateVersion !== null && (
                                <> v{Number(r.resultJson.templateVersion)}</>
                              )}
                              {r.resultJson.templateTaskId && r.resultJson.templateTaskId !== r.resultJson.templateName && (
                                <> ({String(r.resultJson.templateTaskId)})</>
                              )}
                            </span>
                          </div>
                        )}
                      </div>
                      <div className="grid grid-cols-3 gap-2 text-sm sm:grid-cols-4 lg:w-[360px]">
                        <div className="rounded-md bg-gray-50 px-2 py-1.5">
                          <div className="text-[11px] text-gray-500">得分</div>
                          <div className="mt-0.5 font-medium text-gray-900">
                            {toDisplayText(r.score)}/{toDisplayText(r.maxScore ?? 1)}
                          </div>
                        </div>
                        <div className="rounded-md bg-gray-50 px-2 py-1.5">
                          <div className="text-[11px] text-gray-500">Token</div>
                          <div className="mt-0.5 text-gray-900" title={tokenBreakdownText(r.tokenUsage)}>
                            {formatTokenUsage(r.tokenUsage)}
                          </div>
                        </div>
                        <div className="rounded-md bg-gray-50 px-2 py-1.5">
                          <div className="text-[11px] text-gray-500">耗时</div>
                          <div className="mt-0.5 text-gray-900">
                            {toNumberOrNull(r.executionTimeMs) !== null ? (
                              <>
                            {(toNumberOrNull(r.executionTimeMs)! / 1000).toFixed(1)}s
                              </>
                            ) : '-'}
                          </div>
                        </div>
                        <div className="flex items-center justify-center rounded-md bg-gray-50 px-2 py-1.5 text-gray-400">
                          <span className="text-xs">{expandedResultId === r.resultId ? '▲' : '▼'}</span>
                        </div>
                      </div>
                    </div>

                    {expandedResultId === r.resultId && (
                      <div className="border-t border-gray-200 px-4 py-3 space-y-3">
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                          {r.gradingType && (
                            <DetailItem label="评分方式">{toDisplayText(r.gradingType)}</DetailItem>
                          )}
                          {r.resultJson?.templateName && (
                            <DetailItem label="模板" mono>
                                {String(r.resultJson.templateName)}
                                {r.resultJson.templateVersion !== undefined && r.resultJson.templateVersion !== null && (
                                  <> v{Number(r.resultJson.templateVersion)}</>
                                )}
                                {r.resultJson.templateTaskId && r.resultJson.templateTaskId !== r.resultJson.templateName && (
                                  <> ({String(r.resultJson.templateTaskId)})</>
                                )}
                            </DetailItem>
                          )}
                          {r.workspacePath && (
                            <div className="md:col-span-2 xl:col-span-3">
                              <DetailItem label="本地 Workspace" mono>{toDisplayText(r.workspacePath)}</DetailItem>
                            </div>
                          )}
                        </div>

                        {r.errorText && (
                          <div className="rounded-md bg-red-50 p-2 text-sm text-red-700 whitespace-pre-wrap">
                            {toDisplayText(r.errorText)}
                          </div>
                        )}

                        {r.resultJson && (
                          <>
                            {(r.resultJson as Record<string, unknown>).frontmatter && (
                              <JsonBlock label="Frontmatter" data={(r.resultJson as Record<string, unknown>).frontmatter} />
                            )}
                            {(r.resultJson as Record<string, unknown>).usage && (
                              <JsonBlock label="Token 用量" data={(r.resultJson as Record<string, unknown>).usage} />
                            )}
                            {(r.resultJson as Record<string, unknown>).grading && (
                              <JsonBlock label="评分结果" data={(r.resultJson as Record<string, unknown>).grading} />
                            )}
                            {(r.resultJson as Record<string, unknown>).grading?.runs?.[0]?.breakdown && (
                              <JsonBlock label="评分明细" data={(r.resultJson as Record<string, unknown>).grading.runs[0].breakdown} />
                            )}
                            <JsonBlock label="Raw Result JSON" data={r.resultJson} />
                          </>
                        )}

                        {r.breakdown && <JsonBlock label="Breakdown" data={r.breakdown} />}
                        {r.notes && (
                          <div className="text-sm text-gray-700">
                            <span className="text-gray-500">备注: </span>
                            {toDisplayText(r.notes)}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">{benchText.session}</h2>
            {sessionsLoading ? (
              <BenchLoadingState message="正在加载 Session 列表..." />
            ) : sessionsError ? (
              <BenchErrorState message="Session 数据加载失败，不影响评测结果查看" />
            ) : !sessionsData?.sessions?.length ? (
              <BenchEmptyState message={benchText.noSessions} />
            ) : (
              <div className="grid gap-4 md:grid-cols-[260px_1fr]">
                <div className="space-y-2">
                  {sessionsData.sessions.map((session) => {
                    const active = selectedSessionArtifactId === session.artifactId
                    return (
                      <button
                        key={session.artifactId}
                        onClick={() => setSelectedSessionArtifactId(session.artifactId)}
                        className={`w-full rounded-md border px-3 py-2 text-left text-sm ${active ? 'border-blue-300 bg-blue-50 text-blue-900' : 'border-gray-200 hover:bg-gray-50'}`}
                      >
                        <div className="truncate font-mono text-xs">{session.taskId ?? session.filename ?? session.artifactId}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          {session.eventCount ?? '-'} 事件 · {session.totalTokens ? `${session.totalTokens.toLocaleString()} Token` : benchText.noTokenData}
                        </div>
                      </button>
                    )
                  })}
                </div>
                <div className="min-w-0 rounded-md border border-gray-200 bg-white">
                  {sessionLoading ? (
                    <div className="p-4"><BenchLoadingState message="正在加载 Session 详情..." /></div>
                  ) : selectedSession ? (
                    <div>
                      <div className="border-b border-gray-200 px-4 py-3">
                        <div className="truncate font-mono text-xs text-gray-900">
                          {selectedSession.filename ?? selectedSession.artifactId}
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-gray-500">
                          <span>{selectedSession.eventCount ?? '-'} 事件</span>
                          <span>{selectedSession.messageCount ?? '-'} 消息</span>
                          <span>{selectedSession.toolCallCount ?? '-'} 工具调用</span>
                          <span>{selectedSession.totalTokens ? `${selectedSession.totalTokens.toLocaleString()} Token` : benchText.noTokenData}</span>
                        </div>
                      </div>
                      <div className="max-h-[620px] overflow-auto divide-y divide-gray-100">
                        {(selectedSession.events as BenchSessionEvent[]).slice(0, 200).map((event, idx) => (
                          <div key={idx} className="px-4 py-3 text-sm">
                            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                              <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">{eventLabel(event)}</span>
                              <span className="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-blue-700">{eventRole(event)}</span>
                              {event.timestamp && <span className="text-gray-400">{event.timestamp}</span>}
                              {eventUsageText(event) && <span className="text-gray-500">{eventUsageText(event)}</span>}
                            </div>
                            <div className="space-y-2">
                              {eventContentBlocks(event).map((block, blockIdx) => (
                                <div
                                  key={blockIdx}
                                  className={`rounded-md border px-3 py-2 ${
                                    block.tone === 'tool'
                                      ? 'border-purple-100 bg-purple-50'
                                      : block.tone === 'thinking'
                                      ? 'border-yellow-100 bg-yellow-50'
                                      : 'border-gray-100 bg-gray-50'
                                  }`}
                                >
                                  <div className="mb-1 text-xs font-medium text-gray-500">{block.label}</div>
                                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-gray-800">{block.text}</pre>
                                </div>
                              ))}
                            </div>
                            <details className="mt-2">
                              <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">原始事件 JSON</summary>
                              <pre className="mt-1 max-h-80 overflow-auto rounded-md bg-gray-950 p-3 text-xs leading-5 text-gray-100">
                                {JSON.stringify(event, null, 2)}
                              </pre>
                            </details>
                          </div>
                        ))}
                        {(selectedSession.events as BenchSessionEvent[]).length > 200 && (
                          <div className="px-4 py-3 text-xs text-gray-500">仅展示前 200 条事件</div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <BenchEmptyState message="请选择一个 Session" />
                  )}
                </div>
              </div>
            )}
          </section>

          {/* Analysis Report */}
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">{benchText.report}</h2>
            {run.summary?.reportMarkdown ? (
              <div className="space-y-3">
                {run.summary.reportSummary && (
                  <div className="rounded-md bg-blue-50 p-3 text-sm text-blue-800">
                    <span className="font-medium">摘要: </span>
                    {String(run.summary.reportSummary)}
                  </div>
                )}
                {run.summary.reportRiskLevel && (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-gray-500">风险等级:</span>
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                      String(run.summary.reportRiskLevel).toLowerCase() === 'high'
                        ? 'bg-red-100 text-red-700'
                        : String(run.summary.reportRiskLevel).toLowerCase() === 'medium'
                        ? 'bg-yellow-100 text-yellow-700'
                        : 'bg-green-100 text-green-700'
                    }`}>
                      {String(run.summary.reportRiskLevel)}
                    </span>
                  </div>
                )}
                {Array.isArray(run.summary.reportRecommendations) && run.summary.reportRecommendations.length > 0 && (
                  <div className="text-sm">
                    <div className="text-xs font-medium text-gray-700 mb-1">建议</div>
                    <ol className="list-decimal list-inside space-y-1 text-gray-800">
                      {run.summary.reportRecommendations.map((rec: unknown, idx: number) => (
                        <li key={idx}>{String(rec)}</li>
                      ))}
                    </ol>
                  </div>
                )}
                <div className="border-t border-gray-100 pt-3">
                  <div className="max-w-none">
                    <MarkdownReport markdown={String(run.summary.reportMarkdown)} />
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-sm text-gray-500 space-y-2">
                <p>暂无分析报告</p>
                {run.summary?.reportError && (
                  <div className="rounded-md bg-red-50 p-2 text-xs text-red-700">
                    报告生成失败: {String(run.summary.reportError)}
                  </div>
                )}
                {run.summary?.reportPromptPath && (
                  <div className="text-xs text-gray-400">
                    Prompt path: {String(run.summary.reportPromptPath)}
                  </div>
                )}
              </div>
            )}
          </section>

          {/* Raw JSON */}
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">原始 JSON</h2>
            {run.summary && <JsonBlock label="运行摘要" data={run.summary} />}
            {run.runConfig && <JsonBlock label="运行配置" data={run.runConfig} />}
          </section>
        </div>
      </div>
    </div>
  )
}
