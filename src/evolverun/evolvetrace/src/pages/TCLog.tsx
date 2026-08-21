import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTCLogBots, useTCLogQuery, useTCLogTasks, useTCLogTaskSearch, useTCLogTrace } from '../api/hooks'
import { useClientUser } from '../hooks/useClientUser'
import type { TCLogObservation, TCLogQueryParams, TCLogSession, TCLogTaskListParams, TCLogTaskSearchParams, TCLogTaskSearchResponse, TCLogTaskSummary, TCLogTrace, TCLogWorkflowNode, TCLogWorkflowRun } from '../types'

type Mode = 'trace' | 'session' | 'task'
type ObservationTab = 'preview' | 'formatted' | 'json'
type TraceDataSource = 'auto' | 'tc' | 'langfuse'
type EmbedMode = 'session' | 'task'

const DAY_MS = 24 * 60 * 60 * 1000
const CST_OFFSET_MS = 8 * 60 * 60 * 1000
const MAX_QUERY_RANGE_MS = 90 * DAY_MS
const DEFAULT_TRACE_PAGE_SIZE = 20
const TRACE_PAGE_SIZE_OPTIONS = [20, 50, 100] as const
const SESSION_PAGE_SIZE = 20
const TASK_PAGE_SIZE = 20

function defaultFrom() {
  return Date.now() - 7 * DAY_MS
}

function toCSTInputValue(ms: number) {
  if (!Number.isFinite(ms)) return ''
  return new Date(ms + CST_OFFSET_MS).toISOString().slice(0, 16)
}

function fromCSTInputValue(value: string, fallback: number) {
  if (!value) return fallback
  const parsed = Date.parse(`${value}:00.000+08:00`)
  return Number.isFinite(parsed) ? parsed : fallback
}

function formatTime(ms: number | null | undefined) {
  if (!ms) return '-'
  return new Date(ms).toLocaleString()
}

function formatNumber(value: number | null | undefined, digits = 0) {
  if (value == null) return '-'
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function formatDuration(ms: number | null | undefined) {
  if (ms == null || !Number.isFinite(ms)) return '-'
  if (ms < 1000) return `${Math.max(0, ms).toFixed(0)}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(2)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m ${rest}s`
}

function compact(text: string | null | undefined) {
  if (!text) return '-'
  return text.length > 140 ? `${text.slice(0, 140)}...` : text
}

function stringifyValue(value: unknown, limit?: number) {
  if (value == null) return ''
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return limit && text.length > limit ? `${text.slice(0, limit)}...` : text
}

function observationLabel(observation: TCLogObservation) {
  const type = observation.type?.toUpperCase() ?? ''
  const name = observation.name?.toLowerCase() ?? ''
  const metadata = observation.metadata && typeof observation.metadata === 'object' ? observation.metadata as Record<string, unknown> : {}
  const attributes = metadata.attributes && typeof metadata.attributes === 'object' ? metadata.attributes as Record<string, unknown> : {}
  const role = String(metadata.role ?? attributes.role ?? '').toLowerCase()
  const toolName = String(metadata.toolName ?? metadata.tool_name ?? attributes['gen_ai.tool.name'] ?? '').replace(/^tool\s+/i, '').trim()
  const nameTool = observation.name?.match(/^tool\s+(.+)$/i)?.[1]?.trim()
  const agentName = observation.name?.match(/^agent\s+(.+)$/i)?.[1]?.trim()
  if (type === 'TOOL' || name.includes('tool')) return toolName || nameTool ? `tool_call · ${toolName || nameTool}` : 'tool_call'
  if (type === 'AGENT' || name.includes('agent')) return agentName ? `agent · ${agentName}` : 'agent'
  if (role === 'user' || name.includes('user')) return 'user_message'
  if (role === 'assistant' || type === 'LLM' || type === 'CHAT') return 'assistant_message'
  return (observation.name || observation.type || 'observation').toLowerCase()
}

function typeTone(type: string | null | undefined) {
  const t = type?.toUpperCase() ?? ''
  if (t === 'TOOL') return 'border-amber-200 bg-amber-50 text-amber-800'
  if (t === 'LLM') return 'border-blue-200 bg-blue-50 text-blue-800'
  if (t === 'CHAT') return 'border-emerald-200 bg-emerald-50 text-emerald-800'
  if (t === 'AGENT') return 'border-violet-200 bg-violet-50 text-violet-800'
  return 'border-gray-200 bg-gray-50 text-gray-700'
}

function typeDot(type: string | null | undefined) {
  const t = type?.toUpperCase() ?? ''
  if (t === 'TOOL') return 'bg-amber-500'
  if (t === 'LLM') return 'bg-blue-500'
  if (t === 'CHAT') return 'bg-emerald-500'
  if (t === 'AGENT') return 'bg-violet-500'
  return 'bg-gray-400'
}

function toTraceRequestSource(source: string | undefined, fallback: TraceDataSource): TraceDataSource {
  if (source === 'ocb_otel') return 'tc'
  if (source === 'langfuse_legacy') return 'langfuse'
  return fallback
}

function urlParam(params: URLSearchParams, camel: string, snake?: string) {
  return params.get(camel) || (snake ? params.get(snake) : '') || ''
}

function urlDataSource(value: string | null): TraceDataSource {
  return value === 'tc' || value === 'langfuse' ? value : 'auto'
}

async function copyText(value: string | null | undefined) {
  if (!value) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    // Fall back below for non-secure contexts or blocked clipboard permissions.
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    textarea.style.top = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}

export default function TCLog({ embedMode }: { embedMode?: EmbedMode }) {
  const { user } = useClientUser()
  const [searchParams] = useSearchParams()
  const isEmbed = !!embedMode
  const initialMode: Mode = embedMode === 'session' ? 'session' : embedMode === 'task' ? 'task' : 'trace'
  const [mode, setMode] = useState<Mode>(initialMode)
  const [ownerId, setOwnerId] = useState(user?.userId ?? '')
  const [adminMode, setAdminMode] = useState(false)
  const [botId, setBotId] = useState('')
  const [traceId, setTraceId] = useState('')
  const [sessionKey, setSessionKey] = useState(() => urlParam(searchParams, 'sessionKey', 'session_key'))
  const [sessionId, setSessionId] = useState(() => urlParam(searchParams, 'sessionId', 'session_id'))
  const [keyword, setKeyword] = useState('')
  const [bizScene, setBizScene] = useState(() => urlParam(searchParams, 'bizScene', 'biz_scene'))
  const [taskId, setTaskId] = useState(() => urlParam(searchParams, 'taskId', 'biz_task_id'))
  const [dataSource, setDataSource] = useState<TraceDataSource>(() => urlDataSource(searchParams.get('dataSource')))
  const [tracePage, setTracePage] = useState(1)
  const [tracePageSize, setTracePageSize] = useState(DEFAULT_TRACE_PAGE_SIZE)
  const [sessionPage, setSessionPage] = useState(1)
  const [from, setFrom] = useState(defaultFrom)
  const [to, setTo] = useState(Date.now)
  const [submittedQuery, setSubmittedQuery] = useState<TCLogQueryParams | null>(null)
  const [submittedTaskList, setSubmittedTaskList] = useState<TCLogTaskListParams | null>(null)
  const [submittedTask, setSubmittedTask] = useState<TCLogTaskSearchParams | null>(null)
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null)
  const [formError, setFormError] = useState('')

  const canUseAdminMode = (!!user?.isLogAdmin || !!user?.isAdmin) && !isEmbed
  const effectiveOwnerId = isEmbed
    ? ''
    : canUseAdminMode && adminMode
      ? (ownerId || user?.userId || '')
      : (user?.userId || ownerId || '')
  const botsQuery = useTCLogBots({ ownerId: effectiveOwnerId || undefined, status: 'active' }, !isEmbed)

  const queryParams = useMemo(() => ({
    ownerId: effectiveOwnerId || undefined,
    embed: isEmbed || undefined,
    botId: botId || undefined,
    traceId: traceId.trim() || undefined,
    sessionKey: sessionKey.trim() || undefined,
    sessionId: sessionId.trim() || undefined,
    keyword: keyword.trim() || undefined,
    from,
    to,
    dataSource,
    limit: mode === 'trace' ? tracePageSize : SESSION_PAGE_SIZE,
    offset: mode === 'trace' ? (tracePage - 1) * tracePageSize : (sessionPage - 1) * SESSION_PAGE_SIZE,
    groupBy: mode === 'trace' ? 'trace' : 'session',
  }), [botId, dataSource, effectiveOwnerId, from, isEmbed, keyword, mode, sessionId, sessionKey, to, traceId, tracePage, tracePageSize, sessionPage])

  const taskParams = useMemo(() => ({
    ownerId: effectiveOwnerId || undefined,
    embed: isEmbed || undefined,
    botId: botId || undefined,
    bizScene: bizScene.trim() || undefined,
    taskId: taskId.trim() || undefined,
    from,
    to,
    dataSource,
    limit: 100,
  }), [bizScene, botId, dataSource, effectiveOwnerId, from, isEmbed, taskId, to])

  const taskListParams = useMemo(() => ({
    ownerId: effectiveOwnerId,
    botId: botId || undefined,
    bizScene: bizScene.trim() || undefined,
    taskId: taskId.trim() || undefined,
    from,
    to,
    limit: 100,
  }), [bizScene, botId, effectiveOwnerId, from, taskId, to])

  const query = useTCLogQuery(submittedQuery ?? queryParams, submittedQuery !== null && (mode === 'trace' || mode === 'session'))
  const taskList = useTCLogTasks(submittedTaskList ?? taskListParams, mode === 'task' && submittedTaskList !== null && !!effectiveOwnerId)
  const taskSearch = useTCLogTaskSearch(submittedTask ?? taskParams, submittedTask !== null && mode === 'task')

  const activeResult = mode === 'trace' || mode === 'session' ? query.data : taskSearch.data
  const activeLoading = mode === 'trace' || mode === 'session' ? query.isFetching : taskSearch.isFetching
  const activeError = mode === 'trace' || mode === 'session' ? query.error : taskSearch.error
  const resultTraceDataSource = toTraceRequestSource(activeResult?.dataSource, dataSource)
  const visibleTraces = mode === 'trace' || mode === 'session'
    ? (query.data?.traces ?? [])
    : (taskSearch.data?.traces ?? [])
  const selectedTrace = visibleTraces.find((trace) => trace.traceId === selectedTraceId)
  const traceDetail = useTCLogTrace(selectedTraceId, effectiveOwnerId || undefined, resultTraceDataSource, selectedTrace?.botId || botId || undefined, isEmbed)

  useEffect(() => {
    if (!user?.userId) return
    setOwnerId((current) => current || user.userId)
  }, [user?.userId])

  useEffect(() => {
    if (canUseAdminMode) return
    setAdminMode(false)
  }, [canUseAdminMode])

  const submit = () => {
    setFormError('')
    if (to < from) {
      setFormError('结束时间不能早于开始时间。')
      return
    }
    if (to - from > MAX_QUERY_RANGE_MS) {
      setFormError('时间范围最多查询 90 天。')
      return
    }
    if (mode === 'trace' || mode === 'session') {
      setTracePage(1)
      setSessionPage(1)
      setSubmittedQuery({ ...queryParams, offset: 0 })
      return
    }
    if (mode === 'task') {
      setSubmittedTaskList(null)
      setSubmittedTask(null)
      setSubmittedTaskList({ ...taskListParams })
    }
  }

  const visibleTasks = useMemo(() => {
    const tasks = submittedTaskList ? (taskList.data?.tasks ?? []) : []
    if (mode !== 'task' || !submittedTask?.taskId || taskId.trim() !== submittedTask.taskId) return tasks
    const taskKey = `${submittedTask.bizScene || ''}:${submittedTask.taskId}`
    const matched = tasks.find((task) => `${task.bizScene || ''}:${task.taskId}` === taskKey)
      ?? tasks.find((task) => task.taskId === submittedTask.taskId)
    const result = taskSearch.data
    const currentTask: TCLogTaskSummary = {
      bizScene: submittedTask.bizScene || matched?.bizScene || bizScene || '-',
      taskId: submittedTask.taskId,
      botId: submittedTask.botId || matched?.botId || null,
      ownerId: submittedTask.ownerId || matched?.ownerId || effectiveOwnerId || null,
      source: matched?.source || result?.dataSource || '当前查询',
      refCount: matched?.refCount ?? result?.relations.length ?? 0,
      traceCount: result?.summary.traceCount ?? matched?.traceCount ?? 0,
      workflowRunCount: result?.summary.workflowRunCount ?? matched?.workflowRunCount ?? 0,
      lastEventTimeMs: matched?.lastEventTimeMs ?? result?.timeline[0]?.eventTimeMs ?? null,
    }
    return [currentTask]
  }, [bizScene, effectiveOwnerId, mode, submittedTask, submittedTaskList, taskId, taskList.data?.tasks, taskSearch.data])

  useEffect(() => {
    const firstTraceId = visibleTraces[0]?.traceId
    if (!selectedTraceId && firstTraceId) setSelectedTraceId(firstTraceId)
    if (selectedTraceId && visibleTraces.length > 0 && !visibleTraces.some((trace) => trace.traceId === selectedTraceId)) {
      setSelectedTraceId(firstTraceId ?? null)
    }
  }, [selectedTraceId, visibleTraces])

  useEffect(() => {
    if (embedMode === 'session') {
      const nextSessionKey = urlParam(searchParams, 'sessionKey', 'session_key')
      const nextSessionId = urlParam(searchParams, 'sessionId', 'session_id')
      if (!nextSessionKey && !nextSessionId) {
        setFormError('缺少 session_key 或 session_id 参数。')
        return
      }
      const nextDataSource = urlDataSource(searchParams.get('dataSource'))
      setMode('session')
      setSessionKey(nextSessionKey)
      setSessionId(nextSessionId)
      setDataSource(nextDataSource)
      setSubmittedQuery({
        ...queryParams,
        sessionKey: nextSessionKey || undefined,
        sessionId: nextSessionKey ? undefined : nextSessionId || undefined,
        dataSource: nextDataSource,
        groupBy: 'session',
        offset: 0,
        limit: SESSION_PAGE_SIZE,
        embed: true,
      })
      return
    }
    if (embedMode === 'task') {
      const nextBizScene = urlParam(searchParams, 'bizScene', 'biz_scene')
      const nextTaskId = urlParam(searchParams, 'taskId', 'biz_task_id')
      if (!nextBizScene || !nextTaskId) {
        setFormError('缺少 biz_scene 或 biz_task_id 参数。')
        return
      }
      const nextDataSource = urlDataSource(searchParams.get('dataSource'))
      setMode('task')
      setBizScene(nextBizScene)
      setTaskId(nextTaskId)
      setDataSource(nextDataSource)
      setSubmittedTask({
        ...taskParams,
        bizScene: nextBizScene,
        taskId: nextTaskId,
        dataSource: nextDataSource,
        embed: true,
      })
    }
  }, [embedMode, searchParams.toString()])

  return (
    <div className={isEmbed ? 'px-3 py-3 sm:px-4' : 'px-4 py-4 sm:px-6 lg:px-8'}>
      {!isEmbed && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-gray-900">TCLog 日志查询</h1>
            <p className="mt-0.5 text-xs text-gray-500">
              按会话目录查询 OCB 对话日志，或按业务任务 ID 聚合 OCB 与 ClawMind 工作流数据。
            </p>
          </div>
          {activeResult && (
            <div className="rounded-md border border-gray-200 bg-white px-3 py-2 text-xs text-gray-500 shadow-sm">
              数据源：<span className="font-medium text-gray-700">{activeResult.dataSource}</span>
              {activeResult.fallbackUsed && <span className="ml-2 text-amber-600">已使用旧库兜底</span>}
            </div>
          )}
        </div>
      )}

      {!isEmbed && (
      <section className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
        <div className="mb-3 flex flex-wrap gap-2">
          {([
            ['trace', 'Trace 查询'],
            ['session', 'Session 查询'],
            ['task', '业务任务查询'],
          ] as Array<[Mode, string]>).map(([value, label]) => (
            <button
              key={value}
              onClick={() => {
                setMode(value)
                setFormError('')
                if ((value === 'trace' || value === 'session') && submittedQuery) {
                  setTracePage(1)
                  setSessionPage(1)
                  setSubmittedQuery({
                    ...queryParams,
                    groupBy: value,
                    limit: value === 'trace' ? tracePageSize : SESSION_PAGE_SIZE,
                    offset: 0,
                  })
                }
              }}
              className={`rounded-md px-3 py-1 text-sm font-medium ${mode === value ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid gap-2 md:grid-cols-4">
          <Field label="归属人工号">
            <div className="flex gap-2">
              <input
                value={adminMode ? ownerId : (user?.userId ?? ownerId)}
                onChange={(e) => {
                  setOwnerId(e.target.value)
                  setBotId('')
                  setFormError('')
                }}
                disabled={!adminMode}
                className="min-w-0 flex-1 rounded-md border border-gray-300 px-2.5 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-500"
              />
              {canUseAdminMode && (
                <button
                  type="button"
                  onClick={() => {
                    const nextAdminMode = !adminMode
                    setAdminMode(nextAdminMode)
                    setOwnerId(user?.userId ?? '')
                    setBotId('')
                    setFormError('')
                  }}
                  className={`shrink-0 rounded-md border px-2.5 py-1.5 text-xs font-medium ${
                    adminMode
                      ? 'border-amber-300 bg-amber-600 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  {adminMode ? '关闭管理员' : '启用管理员'}
                </button>
              )}
            </div>
          </Field>
          <Field label="Bot">
            {adminMode ? (
              <>
                <input
                  value={botId}
                  list="tclog-admin-bots"
                  onChange={(e) => setBotId(e.target.value)}
                  placeholder="留空表示全部；可输入 bot_id"
                  className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm"
                />
                <datalist id="tclog-admin-bots">
                  {(botsQuery.data?.bots ?? []).map((bot) => (
                    <option key={bot.botId} value={bot.botId}>{bot.displayBotId}</option>
                  ))}
                </datalist>
              </>
            ) : (
              <select
                value={botId}
                onChange={(e) => setBotId(e.target.value)}
                className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm"
              >
                <option value="">全部激活 Bot</option>
                {(botsQuery.data?.bots ?? []).map((bot) => (
                  <option key={bot.botId} value={bot.botId}>{bot.displayBotId}</option>
                ))}
              </select>
            )}
          </Field>
          <TimeRangePicker
            className="md:col-span-2"
            from={from}
            to={to}
            onApply={(nextFrom, nextTo) => {
              setFrom(nextFrom)
              setTo(nextTo)
              setFormError('')
            }}
          />
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-gray-600">日志库</span>
          {([
            ['auto', '自动'],
            ['tc', 'tc库'],
            ['langfuse', 'langfuse库'],
          ] as Array<[TraceDataSource, string]>).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setDataSource(value)}
              className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
                dataSource === value
                  ? 'border-blue-200 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === 'trace' || mode === 'session' ? (
          <div className="mt-2 grid gap-2 md:grid-cols-4">
            <Field label="Trace ID">
              <input value={traceId} onChange={(e) => setTraceId(e.target.value)} className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-sm" />
            </Field>
            <Field label="Session Key">
              <input value={sessionKey} onChange={(e) => setSessionKey(e.target.value)} className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-sm" />
            </Field>
            <Field label="Session ID">
              <input value={sessionId} onChange={(e) => setSessionId(e.target.value)} className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-sm" />
            </Field>
            <Field label="关键词">
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm" />
            </Field>
          </div>
        ) : (
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <Field label="业务场景">
              <input value={bizScene} onChange={(e) => setBizScene(e.target.value)} placeholder="harness_eval" className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm" />
            </Field>
            <Field label="业务任务 ID">
              <input value={taskId} onChange={(e) => setTaskId(e.target.value)} placeholder="case-001 / flow_id / biz_task_id" className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-sm" />
            </Field>
          </div>
        )}

        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={submit}
            disabled={activeLoading}
            className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {activeLoading ? '查询中...' : '查询'}
          </button>
          {mode === 'task' && (
            <span className="text-xs text-gray-400">可输入业务任务 ID 精确查询，或从左侧任务目录点击进入</span>
          )}
          {formError && <span className="text-xs text-red-600">{formError}</span>}
        </div>
      </section>
      )}

      {activeError && (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {activeError instanceof Error ? activeError.message : '查询失败'}
        </div>
      )}

      {activeLoading && (
        <div className="mt-4 rounded-md border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
          正在加载查询结果...
        </div>
      )}

      {(mode === 'trace' || mode === 'session') && query.data && (
        <div className="mt-5 space-y-5">
          {!isEmbed && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <div>
                <div className="text-sm font-semibold text-gray-900">{mode === 'trace' ? 'Trace 查询结果' : 'Session 查询结果'}</div>
                <div className="mt-0.5 text-xs text-gray-500">
                  {mode === 'trace' ? '按 Trace 分页展示匹配日志' : '按 Session 聚合分页展示匹配日志'}
                </div>
              </div>
              <SummaryInline
                items={mode === 'trace'
                  ? [
                      ['会话', query.data.summary.sessionCount],
                      ['Trace', query.data.summary.traceCount],
                      ['Token', query.data.summary.totalTokens],
                      ['成本', query.data.summary.totalCost.toFixed(6)],
                    ]
                  : [
                      ['会话', query.data.summary.sessionCount],
                    ]}
              />
            </div>
          )}
          {mode === 'trace' ? (
            <TraceResultPanel
              traces={query.data.traces}
              selectedTraceId={selectedTraceId}
              onSelectTrace={setSelectedTraceId}
              traceDetail={traceDetail.data?.trace ?? null}
              traceLoading={traceDetail.isFetching}
              page={tracePage}
              pageSize={tracePageSize}
              hasNextPage={query.data.traces.length >= tracePageSize}
              loading={query.isFetching}
              onPageChange={(page) => {
                const nextPage = Math.max(1, page)
                setTracePage(nextPage)
                if (submittedQuery) {
                  setSubmittedQuery({ ...submittedQuery, groupBy: 'trace', offset: (nextPage - 1) * tracePageSize, limit: tracePageSize })
                }
              }}
              onPageSizeChange={(pageSize) => {
                setTracePageSize(pageSize)
                setTracePage(1)
                if (submittedQuery) {
                  setSubmittedQuery({ ...submittedQuery, groupBy: 'trace', offset: 0, limit: pageSize })
                }
              }}
            />
          ) : (
            <SessionDirectory
              sessions={query.data.sessions}
              selectedTraceId={selectedTraceId}
              onSelectTrace={setSelectedTraceId}
              traceDetail={traceDetail.data?.trace ?? null}
              traceLoading={traceDetail.isFetching}
              ownerId={effectiveOwnerId}
              dataSource={resultTraceDataSource}
              page={sessionPage}
              hasNextPage={query.data.sessions.length >= SESSION_PAGE_SIZE}
              loading={query.isFetching}
              onPageChange={(page) => {
                const nextPage = Math.max(1, page)
                setSessionPage(nextPage)
                if (submittedQuery) {
                  setSubmittedQuery({ ...submittedQuery, groupBy: 'session', offset: (nextPage - 1) * SESSION_PAGE_SIZE, limit: SESSION_PAGE_SIZE })
                }
              }}
              embedMode={isEmbed}
            />
          )}
        </div>
      )}

      {mode === 'task' && (
        <TaskWorkspace
          tasks={visibleTasks}
          loading={submittedTask ? taskSearch.isFetching : taskList.isFetching}
          selectedTaskId={submittedTask?.taskId ?? null}
          onSelectTask={(task) => {
            setTaskId(task.taskId)
            setBizScene(task.bizScene)
            setSubmittedTaskList(null)
            setSubmittedTask({
              ...taskParams,
              bizScene: task.bizScene,
              taskId: task.taskId,
              botId: task.botId || taskParams.botId,
            })
            setFormError('')
          }}
          result={submittedTask ? (taskSearch.data ?? null) : null}
          selectedTraceId={selectedTraceId}
          onSelectTrace={setSelectedTraceId}
          traceDetail={traceDetail.data?.trace ?? null}
          traceLoading={traceDetail.isFetching}
          embedMode={isEmbed}
          ownerId={effectiveOwnerId || ''}
          dataSource={resultTraceDataSource}
        />
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-xs font-medium text-gray-600">{label}</span>
      {children}
    </label>
  )
}

function SummaryInline({ items }: { items: Array<[string, string | number]> }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {items.map(([label, value]) => (
        <div key={label} className="rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs">
          <span className="text-gray-400">{label}</span>
          <span className="ml-1 font-semibold text-gray-800">{value}</span>
        </div>
      ))}
    </div>
  )
}

function TraceResultPanel({
  traces,
  selectedTraceId,
  onSelectTrace,
  traceDetail,
  traceLoading,
  page,
  pageSize,
  hasNextPage,
  loading,
  onPageChange,
  onPageSizeChange,
}: {
  traces: TCLogTrace[];
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  traceDetail: TCLogTrace | null;
  traceLoading: boolean;
  page: number;
  pageSize: number;
  hasNextPage: boolean;
  loading: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const [detailOpen, setDetailOpen] = useState(false)
  const displayTraceDetail = traceDetail?.traceId === selectedTraceId ? traceDetail : null
  const displayTraceLoading = traceLoading || (detailOpen && !!selectedTraceId && traceDetail?.traceId !== selectedTraceId)
  const start = traces.length === 0 ? 0 : (page - 1) * pageSize + 1
  const end = (page - 1) * pageSize + traces.length
  return (
    <section className="relative min-h-[780px] overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-gray-900">Trace 列表</div>
            <div className="mt-0.5 text-xs text-gray-500">
              每页 {pageSize} 条；当前显示 {start}-{end}
            </div>
          </div>
          <TracePager
            page={page}
            pageSize={pageSize}
            hasNextPage={hasNextPage}
            loading={loading}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
        <div className="min-h-[720px]">
          <TraceTable
            traces={traces}
            selectedTraceId={detailOpen ? selectedTraceId : null}
            onSelectTrace={(traceId) => {
              onSelectTrace(traceId)
              setDetailOpen(true)
            }}
          />
        </div>
      </div>
      {detailOpen && selectedTraceId && (
        <div className="absolute inset-y-0 right-0 z-30 w-full overflow-hidden border-l border-gray-200 bg-white shadow-2xl xl:w-[72%]">
          <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-gray-900">Trace 详情</div>
              <div className="mt-0.5 text-xs text-gray-500">当前 Trace 的输入、输出和工具调用</div>
            </div>
            <button
              type="button"
              onClick={() => setDetailOpen(false)}
              className="rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-600 hover:bg-gray-50"
              aria-label="关闭 Trace 详情"
            >
              ×
            </button>
          </div>
          <div className="h-[720px] min-h-0 overflow-auto bg-gray-50">
            <TraceDetailPanel trace={displayTraceDetail} loading={displayTraceLoading} />
          </div>
        </div>
      )}
    </section>
  )
}

function TracePager({
  page,
  pageSize,
  hasNextPage,
  loading,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageSize: number;
  hasNextPage: boolean;
  loading: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <label className="flex items-center gap-1">
        <span>每页</span>
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          disabled={loading}
          className="rounded border border-gray-200 bg-white px-1.5 py-1 text-xs text-gray-600 disabled:opacity-50"
        >
          {TRACE_PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>{size}</option>
          ))}
        </select>
      </label>
      <span>第 {page} 页</span>
      <button
        type="button"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1 || loading}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        上一页
      </button>
      <button
        type="button"
        onClick={() => onPageChange(page + 1)}
        disabled={!hasNextPage || loading}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        下一页
      </button>
    </div>
  )
}

function TimeRangePicker({ from, to, onApply, className = '' }: { from: number; to: number; onApply: (from: number, to: number) => void; className?: string }) {
  const [open, setOpen] = useState(false)
  const [draftFrom, setDraftFrom] = useState(from)
  const [draftTo, setDraftTo] = useState(to)
  const [error, setError] = useState('')

  useEffect(() => {
    if (open) return
    setDraftFrom(from)
    setDraftTo(to)
    setError('')
  }, [from, open, to])

  const applyRange = (nextFrom = draftFrom, nextTo = draftTo) => {
    if (nextTo < nextFrom) {
      setError('结束时间不能早于开始时间。')
      return
    }
    if (nextTo - nextFrom > MAX_QUERY_RANGE_MS) {
      setError('时间范围最多查询 90 天。')
      return
    }
    onApply(nextFrom, nextTo)
    setOpen(false)
    setError('')
  }

  const setQuickRange = (days: number) => {
    const nextTo = Date.now()
    const nextFrom = nextTo - days * DAY_MS
    setDraftFrom(nextFrom)
    setDraftTo(nextTo)
    applyRange(nextFrom, nextTo)
  }

  return (
    <div className={`relative ${className}`}>
      <span className="mb-0.5 block text-xs font-medium text-gray-600">时间范围（CST）</span>
      <button
        type="button"
        onClick={() => {
          setDraftFrom(from)
          setDraftTo(to)
          setError('')
          setOpen((value) => !value)
        }}
        className="flex w-full items-center justify-between gap-3 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-left text-sm hover:bg-gray-50"
      >
        <span className="min-w-0 truncate">{formatTime(from)} 至 {formatTime(to)}</span>
        <span className="shrink-0 text-xs text-gray-400">最大 90 天</span>
      </button>
      {open && (
        <div className="absolute left-0 z-40 mt-1 w-[520px] max-w-[min(520px,calc(100vw-3rem))] rounded-lg border border-gray-200 bg-white p-3 shadow-xl">
          <div className="mb-3 flex flex-wrap gap-2">
            {[1, 7, 30, 90].map((days) => (
              <button
                key={days}
                type="button"
                onClick={() => setQuickRange(days)}
                className="rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
              >
                最近 {days} 天
              </button>
            ))}
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <label className="block">
              <span className="mb-0.5 block text-xs font-medium text-gray-600">开始</span>
              <input
                type="datetime-local"
                value={toCSTInputValue(draftFrom)}
                min={toCSTInputValue(draftTo - MAX_QUERY_RANGE_MS)}
                max={toCSTInputValue(draftTo)}
                onChange={(event) => {
                  setDraftFrom(fromCSTInputValue(event.target.value, draftFrom))
                  setError('')
                }}
                className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm"
              />
            </label>
            <label className="block">
              <span className="mb-0.5 block text-xs font-medium text-gray-600">结束</span>
              <input
                type="datetime-local"
                value={toCSTInputValue(draftTo)}
                min={toCSTInputValue(draftFrom)}
                max={toCSTInputValue(draftFrom + MAX_QUERY_RANGE_MS)}
                onChange={(event) => {
                  setDraftTo(fromCSTInputValue(event.target.value, draftTo))
                  setError('')
                }}
                className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="text-xs text-gray-500">
              当前跨度 {Math.max(0, Math.ceil((draftTo - draftFrom) / DAY_MS))} 天
              {error && <span className="ml-2 text-red-600">{error}</span>}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => applyRange()}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              >
                应用
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function groupTracesAsSessions(traces: TCLogTrace[]): TCLogSession[] {
  const byKey = new Map<string, TCLogSession>()
  const traceTime = (trace: TCLogTrace) => trace.endTimeMs ?? trace.startTimeMs ?? 0
  for (const trace of traces) {
    const key = trace.sessionKey || trace.sessionId || 'unknown-session'
    const session = byKey.get(key) ?? {
      sessionKey: trace.sessionKey,
      sessionId: trace.sessionId,
      ownerId: trace.ownerId,
      botId: trace.botId,
      engine: trace.engine,
      traceCount: 0,
      startTimeMs: trace.startTimeMs,
      endTimeMs: trace.endTimeMs,
      totalTokens: 0,
      totalCost: 0,
      latestStatus: trace.status,
      traces: [],
    }
    session.traces.push(trace)
    session.traceCount = session.traces.length
    session.startTimeMs = Math.min(session.startTimeMs ?? trace.startTimeMs ?? 0, trace.startTimeMs ?? session.startTimeMs ?? 0) || null
    session.endTimeMs = Math.max(session.endTimeMs ?? trace.endTimeMs ?? 0, trace.endTimeMs ?? session.endTimeMs ?? 0) || null
    session.totalTokens = (session.totalTokens ?? 0) + (trace.totalTokens ?? 0)
    session.totalCost = (session.totalCost ?? 0) + (trace.totalCost ?? 0)
    session.latestStatus = trace.status ?? session.latestStatus
    byKey.set(key, session)
  }
  byKey.forEach((session) => {
    session.traces.sort((a, b) => traceTime(b) - traceTime(a))
  })
  return Array.from(byKey.values()).sort((a, b) => (a.startTimeMs ?? 0) - (b.startTimeMs ?? 0))
}

function SessionDirectory({
  sessions,
  stats,
  selectedTraceId,
  onSelectTrace,
  traceDetail,
  traceLoading,
  ownerId = '',
  dataSource = 'auto',
  page = 1,
  hasNextPage = false,
  loading = false,
  onPageChange = () => undefined,
  embedMode = false,
}: {
  sessions: TCLogSession[];
  stats?: Array<[string, string | number]>;
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  traceDetail: TCLogTrace | null;
  traceLoading: boolean;
  ownerId?: string;
  dataSource?: TraceDataSource;
  page?: number;
  hasNextPage?: boolean;
  loading?: boolean;
  onPageChange?: (page: number) => void;
  embedMode?: boolean;
}) {
  const sessionKeyOf = (session: TCLogSession) => session.sessionKey || session.sessionId || 'unknown-session'
  const [selectedSessionKey, setSelectedSessionKey] = useState<string | null>(null)
  const [tracePanelOpen, setTracePanelOpen] = useState(false)
  const selectedSession = useMemo(() => {
    if (sessions.length === 0) return null
    const bySession = selectedSessionKey ? sessions.find((session) => sessionKeyOf(session) === selectedSessionKey) : null
    return bySession ?? null
  }, [selectedSessionKey, selectedTraceId, sessions])
  const sessionTraceQuery = useTCLogQuery({
    ownerId,
    botId: selectedSession?.botId || undefined,
    sessionKey: selectedSession?.sessionKey || undefined,
    sessionId: selectedSession?.sessionKey ? undefined : selectedSession?.sessionId || undefined,
    dataSource,
    limit: 200,
    offset: 0,
    groupBy: 'trace',
    embed: !ownerId || undefined,
  }, !!selectedSession && (!!selectedSession.sessionKey || !!selectedSession.sessionId))
  const selectedSessionTraces = sessionTraceQuery.data?.traces ?? selectedSession?.traces ?? []
  const displayTraceDetail = traceDetail?.traceId === selectedTraceId ? traceDetail : null
  const displayTraceLoading =
    traceLoading || (tracePanelOpen && !!selectedTraceId && traceDetail?.traceId !== selectedTraceId)

  useEffect(() => {
    if (selectedSessionKey && !sessions.some((session) => sessionKeyOf(session) === selectedSessionKey)) {
      setSelectedSessionKey(null)
    }
  }, [selectedSessionKey, sessions])
  useEffect(() => {
    if (embedMode && !selectedSessionKey && sessions[0]) {
      setSelectedSessionKey(sessionKeyOf(sessions[0]))
    }
  }, [embedMode, selectedSessionKey, sessions])
  useEffect(() => {
    setTracePanelOpen(false)
  }, [selectedSessionKey])
  useEffect(() => {
    if (!embedMode || selectedSessionTraces.length === 0) return
    const firstTraceId = selectedSessionTraces[0]?.traceId
    if (!selectedTraceId || !selectedSessionTraces.some((trace) => trace.traceId === selectedTraceId)) {
      onSelectTrace(firstTraceId)
    }
  }, [embedMode, onSelectTrace, selectedSessionTraces, selectedTraceId])

  if (sessions.length === 0) return <EmptyState text="没有找到会话记录" />
  if (embedMode) {
    const session = selectedSession ?? sessions[0]
    if (!session) return <EmptyState text="没有找到会话记录" />
    return (
      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="text-sm font-semibold text-gray-900">会话详情</div>
          <div className="mt-2 grid gap-1 text-xs">
            <CopyLine label="sessionKey" value={session.sessionKey} />
            <CopyLine label="sessionId" value={session.sessionId} />
            <CopyLine label="归属人" value={session.ownerId} />
            <CopyLine label="bot" value={session.botId} />
          </div>
        </div>
        <div className="grid min-h-[640px] lg:grid-cols-[minmax(320px,0.55fr)_minmax(0,1fr)]">
          <div className="min-h-0 border-gray-200 bg-white lg:border-r">
            <div className="h-full min-h-0 overflow-auto">
              <TraceTable
                traces={selectedSessionTraces}
                selectedTraceId={selectedTraceId}
                onSelectTrace={onSelectTrace}
                compactMode
              />
              {sessionTraceQuery.isFetching && (
                <div className="px-4 py-3 text-xs text-blue-700">正在加载会话 Trace...</div>
              )}
            </div>
          </div>
          <div className="min-h-[640px] min-w-0 bg-gray-50">
            {selectedTraceId ? (
              <>
                <div className="border-b border-gray-200 bg-white px-4 py-2">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900">Trace 详情</div>
                    <div className="mt-0.5 text-xs text-gray-500">当前 Turn 的调用明细</div>
                  </div>
                </div>
                <div className="h-[calc(640px-41px)] min-h-0 overflow-auto">
                  <TraceDetailPanel trace={displayTraceDetail} loading={displayTraceLoading} />
                </div>
              </>
            ) : (
              <div className="flex h-full min-h-[640px] items-center justify-center p-6">
                <div className="min-w-0">
                  <EmptyState text="选择一条 Trace 查看详情" />
                </div>
              </div>
            )}
          </div>
        </div>
      </section>
    )
  }
  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">会话目录</div>
          <div className="mt-0.5 text-xs text-gray-500">服务端按会话分页；每页 {SESSION_PAGE_SIZE} 个；点击会话后加载该会话 Trace</div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {stats?.map(([label, value]) => (
            <div key={label} className="rounded border border-gray-200 bg-gray-50 px-2 py-1 text-xs">
              <span className="text-gray-400">{label}</span>
              <span className="ml-1 font-semibold text-gray-800">{value}</span>
            </div>
          ))}
          <SessionPager page={page} hasNextPage={hasNextPage} loading={loading} onPageChange={onPageChange} />
        </div>
      </div>

      <div className="relative h-[720px]">
        <div className="h-full min-h-0 overflow-auto">
          <div className="sticky top-0 z-10 hidden grid-cols-[minmax(0,1fr)_140px_110px_110px] gap-3 border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 lg:grid">
            <div>会话</div>
            <div>会话开始</div>
            <div>最近结束</div>
            <div>操作</div>
          </div>
          {sessions.map((session) => {
            const key = sessionKeyOf(session)
            const active = selectedSession && sessionKeyOf(selectedSession) === key
            return (
              <div
                key={key}
                className={`border-b border-gray-100 px-4 py-3 hover:bg-gray-50 ${active ? 'bg-blue-50' : 'bg-white'}`}
              >
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    setSelectedSessionKey(key)
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    setSelectedSessionKey(key)
                  }}
                  className="block w-full text-left"
                >
                  <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_140px_110px_110px] lg:items-center">
                    <div className="min-w-0">
                      <SessionIdentifier session={session} active={!!active} />
                      <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-500">
                        <span>{session.botId || '-'}</span>
                        <span>{session.engine || '-'}</span>
                        <span>{session.ownerId || '-'}</span>
                      </div>
                    </div>
                    <div className="text-xs text-gray-500">{formatTime(session.startTimeMs)}</div>
                    <div className="text-xs text-gray-500">{formatTime(session.endTimeMs)}</div>
                    <div className="text-xs text-gray-500">点击加载 Trace</div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {selectedSession && (
          <div className="absolute inset-y-0 right-0 z-20 w-full min-h-0 overflow-hidden border-l border-gray-200 bg-gray-50 shadow-2xl xl:w-[78%]">
            <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-gray-900">会话画布</div>
                <div className="mt-0.5 text-xs text-gray-500">Trace 按时间从早到晚排列，点击查看明细</div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedSessionKey(null)}
                className="rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-600 hover:bg-gray-50"
                aria-label="关闭 session 画布"
              >
                ×
              </button>
            </div>
            <div className="relative h-[calc(720px-41px)] min-h-0">
              <div className="h-full min-h-0 overflow-auto bg-white">
                <div className="border-b border-gray-100 px-4 py-3">
                  <div className="text-sm font-semibold text-gray-900">会话详情</div>
                  <div className="mt-2 grid gap-1 text-xs">
                    <CopyLine label="sessionKey" value={selectedSession.sessionKey} />
                    <CopyLine label="sessionId" value={selectedSession.sessionId} />
                    <CopyLine label="归属人" value={selectedSession.ownerId} />
                    <CopyLine label="bot" value={selectedSession.botId} />
                  </div>
                </div>
                <TraceTable
                  traces={selectedSessionTraces}
                  selectedTraceId={tracePanelOpen ? selectedTraceId : null}
                  onSelectTrace={(traceId) => {
                    onSelectTrace(traceId)
                    setTracePanelOpen(true)
                  }}
                  compactMode
                />
                {sessionTraceQuery.isFetching && (
                  <div className="px-4 py-3 text-xs text-blue-700">正在加载会话 Trace...</div>
                )}
              </div>
              {tracePanelOpen && selectedTraceId && (
                <div className="absolute inset-y-0 right-0 z-30 w-full overflow-hidden border-l border-gray-200 bg-white shadow-2xl 2xl:w-[72%]">
                  <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-gray-900">Trace 详情</div>
                      <div className="mt-0.5 text-xs text-gray-500">当前 Turn 的调用明细</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setTracePanelOpen(false)}
                      className="rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-600 hover:bg-gray-50"
                      aria-label="关闭 Trace 详情"
                    >
                      ×
                    </button>
                  </div>
                  <div className="h-[calc(720px-82px)] min-h-0 overflow-auto bg-gray-50">
                    <TraceDetailPanel trace={displayTraceDetail} loading={displayTraceLoading} />
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function SessionIdentifier({ session, active }: { session: TCLogSession; active: boolean }) {
  const missingSession = !session.sessionKey && !session.sessionId
  const primary = session.sessionKey || session.sessionId || '无会话标识'
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className={`min-w-0 break-all font-mono text-xs font-semibold ${active ? 'text-blue-700' : 'text-gray-800'}`}>
        {primary}
      </span>
      {missingSession && <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">unknown-session</span>}
      <CopyPill value={session.sessionKey} label="key" title="复制 sessionKey" />
      <CopyPill value={session.sessionId} label="id" title="复制 sessionId" />
    </div>
  )
}

function Pager({ page, pageCount, total, onPageChange }: { page: number; pageCount: number; total: number; onPageChange: (page: number) => void }) {
  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span>共 {total} 条</span>
      <button
        type="button"
        onClick={() => onPageChange(Math.max(1, page - 1))}
        disabled={page <= 1}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        上一页
      </button>
      <span>{page}/{pageCount}</span>
      <button
        type="button"
        onClick={() => onPageChange(Math.min(pageCount, page + 1))}
        disabled={page >= pageCount}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        下一页
      </button>
    </div>
  )
}

function SessionPager({ page, hasNextPage, loading, onPageChange }: { page: number; hasNextPage: boolean; loading: boolean; onPageChange: (page: number) => void }) {
  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span>第 {page} 页</span>
      <button
        type="button"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1 || loading}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        上一页
      </button>
      <button
        type="button"
        onClick={() => onPageChange(page + 1)}
        disabled={!hasNextPage || loading}
        className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
      >
        下一页
      </button>
    </div>
  )
}

function TraceTable({ traces, selectedTraceId, onSelectTrace, compactMode = false }: { traces: TCLogTrace[]; selectedTraceId?: string | null; onSelectTrace?: (traceId: string) => void; compactMode?: boolean }) {
  if (traces.length === 0) return <EmptyState text="没有 Trace" />
  if (compactMode) {
    return (
      <div className="max-h-[560px] overflow-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-100 bg-white px-4 py-2 text-xs font-medium text-gray-500">
          <span>Trace 列表</span>
          <span>{traces.length}</span>
        </div>
        <div className="divide-y divide-gray-100">
          {traces.map((trace, index) => (
            <div
              key={trace.traceId}
              role="button"
              tabIndex={0}
              onClick={() => onSelectTrace?.(trace.traceId)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                event.preventDefault()
                onSelectTrace?.(trace.traceId)
              }}
              className={`block w-full px-4 py-3 text-left hover:bg-gray-50 ${selectedTraceId === trace.traceId ? 'bg-blue-50' : 'bg-white'}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-medium text-gray-500">#{index + 1}</span>
                    <span className="truncate text-sm font-semibold text-gray-900">{trace.name || trace.engine || 'Trace'}</span>
                    <StatusText status={trace.status} />
                  </div>
                  <div className="mt-1">
                    <CopyInline value={trace.traceId} label="复制" className="text-blue-700" />
                  </div>
                </div>
                <div className="shrink-0 text-right text-xs text-gray-500">
                  <div>{formatTime(trace.startTimeMs)}</div>
                  <div>{formatDuration(trace.latencyMs)}</div>
                </div>
              </div>
              <div className="mt-2 grid gap-1 text-xs text-gray-600">
                <div className="line-clamp-2">{compact(trace.inputPreview)}</div>
                <div className="line-clamp-2 text-gray-400">{compact(trace.outputPreview)}</div>
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-gray-500">
                <TokenBadge trace={trace} />
                <span>{trace.engine || '-'}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-100 text-sm">
        <thead className="bg-gray-50 text-left text-xs font-medium uppercase text-gray-500">
          <tr>
            <th className="px-4 py-2">Trace / Session</th>
            <th className="px-4 py-2">时间</th>
            <th className="px-4 py-2">状态</th>
            <th className="px-4 py-2">Input</th>
            <th className="px-4 py-2">Output</th>
            <th className="px-4 py-2">Token</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {traces.map((trace) => (
            <tr
              key={trace.traceId}
              onClick={() => onSelectTrace?.(trace.traceId)}
              className={`cursor-pointer hover:bg-gray-50 ${selectedTraceId === trace.traceId ? 'bg-blue-50' : ''}`}
            >
              <td className="px-4 py-2">
                <CopyInline value={trace.traceId} label="复制" className="text-blue-700" />
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-400">
                  <span>{trace.name || trace.engine || '-'}</span>
                  <span>{trace.engine || '-'}</span>
                </div>
                {!compactMode && (
                  <div className="mt-1 grid gap-1 text-xs">
                    <CopyLine label="sessionKey" value={trace.sessionKey} />
                    <CopyLine label="sessionId" value={trace.sessionId} />
                  </div>
                )}
              </td>
              <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{formatTime(trace.startTimeMs)}</td>
              <td className="px-4 py-2"><StatusText status={trace.status} /></td>
              <td className="max-w-xs px-4 py-2 text-xs text-gray-600">{compact(trace.inputPreview)}</td>
              <td className="max-w-xs px-4 py-2 text-xs text-gray-600">{compact(trace.outputPreview)}</td>
              <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
                <TokenBadge trace={trace} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TokenBadge({ trace }: { trace: TCLogTrace }) {
  const [open, setOpen] = useState(false)
  const items: Array<[string, number | null | undefined]> = [
    ['total', trace.totalTokens],
    ['input', trace.inputTokens],
    ['output', trace.outputTokens],
    ['cache read', trace.cacheReadTokens],
    ['cache write', trace.cacheWriteTokens],
  ]
  return (
    <span className="relative inline-flex items-center gap-1">
      <span>{formatNumber(trace.totalTokens)} Token</span>
      <button
        type="button"
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          setOpen((value) => !value)
        }}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-200 bg-white text-[10px] font-semibold text-gray-500 hover:bg-gray-50"
        title="查看 token 明细"
      >
        !
      </button>
      {open && (
        <span className="absolute right-0 top-5 z-30 w-44 rounded-md border border-gray-200 bg-white p-2 text-left text-[11px] text-gray-600 shadow-lg">
          {items.map(([label, value]) => (
            <span key={label} className="flex justify-between gap-3 py-0.5">
              <span className="text-gray-400">{label}</span>
              <span className="font-mono text-gray-800">{formatNumber(value)}</span>
            </span>
          ))}
        </span>
      )}
    </span>
  )
}

function TaskWorkspace({
  tasks,
  loading,
  selectedTaskId,
  onSelectTask,
  result,
  selectedTraceId,
  onSelectTrace,
  traceDetail,
  traceLoading,
  embedMode = false,
  ownerId,
  dataSource,
}: {
  tasks: TCLogTaskSummary[];
  loading: boolean;
  selectedTaskId: string | null;
  onSelectTask: (task: TCLogTaskSummary) => void;
  result: TCLogTaskSearchResponse | null;
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  traceDetail: TCLogTrace | null;
  traceLoading: boolean;
  embedMode?: boolean;
  ownerId: string;
  dataSource: TraceDataSource;
}) {
  const sessions = useMemo(() => groupTracesAsSessions(result?.traces ?? []), [result?.traces])
  if (embedMode) {
    return (
      <div className="space-y-5">
        {!result ? (
          <section className="h-[720px] rounded-lg border border-dashed border-gray-200 bg-white shadow-sm">
            <EmptyState text={loading ? '正在加载业务任务...' : '没有找到业务任务'} />
          </section>
        ) : (
          <>
            <SessionDirectory
              sessions={sessions}
              stats={[
                ['会话', sessions.length],
                ['Trace', result.summary.traceCount],
                ['工作流', result.summary.workflowRunCount],
                ['成本', result.summary.totalCost.toFixed(6)],
              ]}
              selectedTraceId={selectedTraceId}
              onSelectTrace={onSelectTrace}
              traceDetail={traceDetail}
              traceLoading={traceLoading}
              ownerId={ownerId}
              dataSource={dataSource}
            />
            <WorkflowRunsPanel runs={result.workflowRuns} />
          </>
        )}
      </div>
    )
  }
  return (
    <div className="mt-5 grid min-h-[720px] gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <TaskList tasks={tasks} loading={loading} selectedTaskId={selectedTaskId} onSelectTask={onSelectTask} />

      <div className="min-w-0 space-y-5">
        {!result ? (
          <section className="h-[720px] rounded-lg border border-dashed border-gray-200 bg-white shadow-sm">
            <EmptyState text="从左侧业务任务目录选择一个任务，或输入业务任务 ID 查询" />
          </section>
        ) : (
          <>
            <SessionDirectory
              sessions={sessions}
              stats={[
                ['会话', sessions.length],
                ['Trace', result.summary.traceCount],
                ['工作流', result.summary.workflowRunCount],
                ['成本', result.summary.totalCost.toFixed(6)],
              ]}
              selectedTraceId={selectedTraceId}
              onSelectTrace={onSelectTrace}
              traceDetail={traceDetail}
              traceLoading={traceLoading}
              ownerId={ownerId}
              dataSource={dataSource}
            />

            <WorkflowRunsPanel runs={result.workflowRuns} />
          </>
        )}
      </div>
    </div>
  )
}

function WorkflowRunsPanel({ runs }: { runs: TCLogWorkflowRun[] }) {
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null)
  const selectedRun = useMemo(() => {
    if (!selectedFlowId) return null
    return runs.find((run) => run.flowId === selectedFlowId) ?? null
  }, [runs, selectedFlowId])

  useEffect(() => {
    if (selectedFlowId && !runs.some((run) => run.flowId === selectedFlowId)) {
      setSelectedFlowId(null)
    }
  }, [runs, selectedFlowId])

  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-4 py-3">
        <div className="font-medium text-gray-900">工作流运行</div>
        <div className="mt-1 text-xs text-gray-500">点击工作流后在右侧打开节点画布；节点输入输出默认折叠</div>
      </div>
      {runs.length === 0 ? (
        <EmptyState text="没有找到工作流运行记录" />
      ) : (
        <div className={`grid h-[640px] ${selectedRun ? 'xl:grid-cols-[minmax(320px,0.74fr)_minmax(0,1.26fr)]' : 'grid-cols-1'}`}>
          <div className="min-h-0 overflow-auto border-gray-200 xl:border-r">
            <div className="divide-y divide-gray-100">
              {runs.map((run) => {
                const active = selectedFlowId === run.flowId
                return (
                  <div
                    key={run.flowId}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedFlowId(run.flowId)}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      setSelectedFlowId(run.flowId)
                    }}
                    className={`px-4 py-3 hover:bg-gray-50 ${active ? 'bg-blue-50' : 'bg-white'}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-gray-900">{run.workflowTitle || run.workflowId}</div>
                        <div className="mt-1">
                          <CopyInline value={run.flowId} label="复制" className="text-gray-500" />
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-gray-500">
                          <span className="rounded bg-gray-100 px-1.5 py-0.5">{run.nodes.length} 个节点</span>
                          <span className="rounded bg-gray-100 px-1.5 py-0.5">{formatTime(run.startedAt)}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <StatusText status={run.status} />
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
          {selectedRun && (
            <WorkflowNodeCanvas run={selectedRun} onClose={() => setSelectedFlowId(null)} />
          )}
        </div>
      )}
    </section>
  )
}

function WorkflowNodeCanvas({ run, onClose }: { run: TCLogWorkflowRun; onClose: () => void }) {
  const [expandedNodeKey, setExpandedNodeKey] = useState<string | null>(null)

  useEffect(() => {
    setExpandedNodeKey(null)
  }, [run.flowId])

  return (
    <div className="min-h-0 overflow-hidden bg-gray-50">
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">节点画布</div>
          <div className="mt-0.5">
            <CopyInline value={run.flowId} label="复制" className="text-gray-500" />
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-gray-200 bg-white px-2 py-1 text-sm text-gray-600 hover:bg-gray-50"
          aria-label="关闭节点画布"
        >
          ×
        </button>
      </div>
      <div className="h-[calc(640px-41px)] min-h-0 overflow-auto">
        <div className="border-b border-gray-100 bg-white px-4 py-3">
          <div className="grid gap-2 text-xs md:grid-cols-2 2xl:grid-cols-3">
            <CopyLine label="workflowId" value={run.workflowId} />
            <CopyLine label="工作流名称" value={run.workflowTitle} />
            <CopyLine label="归属人" value={run.ownerId} />
            <CopyLine label="bot" value={run.botId} />
            <CopyLine label="sessionKey" value={run.originSessionKey} />
            <CopyLine label="sessionId" value={run.originSessionId} />
            <CopyLine label="开始时间" value={formatTime(run.startedAt)} />
            <CopyLine label="结束时间" value={formatTime(run.completedAt)} />
            <CopyLine label="节点数" value={String(run.nodeCount ?? 0)} />
            <CopyLine label="失败数" value={String(run.failedCount ?? 0)} />
            <CopyLine label="当前阶段" value={run.currentPhase} />
            <CopyLine label="总耗时" value={formatDuration(run.totalDurationMs)} />
            <CopyLine label="总 Token" value={run.totalTokenUsage == null ? null : String(run.totalTokenUsage)} />
            <CopyLine label="命中方式" value={run.matchTypes.join(', ') || null} />
          </div>
          <div className="mt-4 grid gap-4 2xl:grid-cols-3">
            <ValueSection title="工作流参数" value={run.params} defaultText="undefined" />
            <ValueSection title="工作流输入" value={run.input} tone="blue" defaultText="undefined" />
            <ValueSection title="工作流输出" value={run.output} tone="green" defaultText="undefined" />
          </div>
        </div>

        <div className="bg-white">
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-100 bg-white px-4 py-2 text-xs font-medium text-gray-500">
            <span>节点执行</span>
            <span>{run.nodes.length}</span>
          </div>
          {run.nodes.length === 0 ? (
            <EmptyState text="没有节点执行记录" />
          ) : (
            <div className="divide-y divide-gray-100">
              {run.nodes.map((node, index) => {
                const nodeKey = `${node.nodeId}:${node.attempt}:${node.id ?? index}`
                const expanded = expandedNodeKey === nodeKey
                return (
                  <WorkflowNodeRow
                    key={nodeKey}
                    index={index}
                    node={node}
                    expanded={expanded}
                    onToggle={() => setExpandedNodeKey(expanded ? null : nodeKey)}
                  />
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function WorkflowNodeRow({ node, index, expanded, onToggle }: { node: TCLogWorkflowNode; index: number; expanded: boolean; onToggle: () => void }) {
  return (
    <div className={expanded ? 'bg-blue-50/40' : 'bg-white'}>
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          onToggle()
        }}
        className="px-4 py-3 hover:bg-gray-50"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">#{index + 1}</span>
              <span className="min-w-0 break-all text-sm font-semibold text-gray-900">{node.nodeTitle || node.nodeId}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-gray-500">
              <CopyInline value={node.nodeId} label="复制" className="text-gray-500" />
              <span>{node.executorType || '-'}</span>
              <span>第 {node.attempt} 次</span>
              <span>{formatDuration(node.durationMs)}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusText status={node.status} />
            <span className="text-xs text-gray-400">{expanded ? '收起' : '展开'}</span>
          </div>
        </div>
      </div>
      {expanded && (
        <div className="border-t border-blue-100 bg-white px-4 py-3">
          <div className="grid gap-2 text-xs md:grid-cols-2 2xl:grid-cols-3">
            <CopyLine label="sessionKey" value={node.sessionKey} />
            <CopyLine label="sessionId" value={node.sessionId} />
            <CopyLine label="embeddedSessionKey" value={node.embeddedSessionKey} />
            <CopyLine label="开始时间" value={formatTime(node.startedAt)} />
            <CopyLine label="结束时间" value={formatTime(node.completedAt)} />
            <CopyLine label="进度信息" value={node.progressMessage} />
          </div>
          <div className="mt-4 grid gap-4 2xl:grid-cols-2">
            <ValueSection title="节点输入" value={node.input} tone="blue" defaultText="undefined" />
            <ValueSection title="节点输出" value={node.output} tone="green" defaultText="undefined" />
            <ValueSection title="错误信息" value={node.errorText} defaultText="undefined" />
            <ValueSection title="Token 用量" value={node.tokenUsage} defaultText="undefined" />
            <ValueSection title="系统上下文" value={node.systemContext} defaultText="undefined" />
          </div>
        </div>
      )}
    </div>
  )
}

function TaskList({ tasks, loading, selectedTaskId, onSelectTask }: { tasks: TCLogTaskSummary[]; loading: boolean; selectedTaskId: string | null; onSelectTask: (task: TCLogTaskSummary) => void }) {
  const [page, setPage] = useState(1)
  const pageCount = Math.max(1, Math.ceil(tasks.length / TASK_PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const pagedTasks = useMemo(() => {
    const start = (safePage - 1) * TASK_PAGE_SIZE
    return tasks.slice(start, start + TASK_PAGE_SIZE)
  }, [safePage, tasks])

  useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])

  return (
    <aside className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-gray-900">业务任务目录</div>
            <div className="mt-1 text-xs text-gray-500">当前归属人 / Bot 下的任务列表</div>
          </div>
          {loading && <span className="text-xs text-gray-400">加载中...</span>}
        </div>
        {tasks.length > 0 && (
          <div className="mt-3">
            <Pager page={safePage} pageCount={pageCount} total={tasks.length} onPageChange={setPage} />
          </div>
        )}
      </div>
      {tasks.length === 0 ? (
        <EmptyState text={loading ? '正在加载业务任务...' : '没有找到业务任务'} />
      ) : (
        <div className="h-[640px] overflow-auto">
          {pagedTasks.map((task) => {
            const active = selectedTaskId === task.taskId
            return (
              <div
                key={`${task.bizScene}:${task.taskId}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelectTask(task)}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter' && event.key !== ' ') return
                  event.preventDefault()
                  onSelectTask(task)
                }}
                className={`border-b border-gray-100 px-4 py-3 text-left hover:bg-gray-50 ${active ? 'bg-blue-50' : 'bg-white'}`}
              >
                <div className="min-w-0">
                  <CopyInline value={task.taskId} label="复制" className={active ? 'text-blue-700' : 'text-gray-900'} strong />
                  <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-gray-500">
                    <span className="rounded bg-gray-100 px-1.5 py-0.5">{task.bizScene}</span>
                    <span className="rounded bg-gray-100 px-1.5 py-0.5">{task.botId || '-'}</span>
                    <span className="rounded bg-gray-100 px-1.5 py-0.5">{task.source}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-500">
                    <span>{task.traceCount} 条 Trace</span>
                    <span>{task.workflowRunCount} 个工作流</span>
                    <span className="col-span-2">{formatTime(task.lastEventTimeMs)}</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </aside>
  )
}

function TraceDetailPanel({ trace, loading }: { trace: TCLogTrace | null; loading: boolean }) {
  const [selectedObservationId, setSelectedObservationId] = useState<string | null>(null)
  const [timelineSearch, setTimelineSearch] = useState('')
  const [activeTab, setActiveTab] = useState<ObservationTab>('preview')

  const observations = trace?.observations ?? []
  const selectedObservation = observations.find((obs) => obs.observationId === selectedObservationId) ?? observations[0] ?? null
  const filteredObservations = useMemo(() => {
    const keyword = timelineSearch.trim().toLowerCase()
    if (!keyword) return observations
    return observations.filter((obs) => [
      obs.observationId,
      obs.parentObservationId,
      obs.name,
      obs.type,
      obs.model,
      observationLabel(obs),
      stringifyValue(obs.input, 600),
      stringifyValue(obs.output, 600),
      stringifyValue(obs.metadata, 600),
    ].filter(Boolean).join(' ').toLowerCase().includes(keyword))
  }, [observations, timelineSearch])

  useEffect(() => {
    const firstObservationId = observations[0]?.observationId ?? null
    if (!trace) {
      setSelectedObservationId(null)
      return
    }
    if (!selectedObservationId || !observations.some((obs) => obs.observationId === selectedObservationId)) {
      setSelectedObservationId(firstObservationId)
    }
  }, [observations, selectedObservationId, trace])

  if (loading) {
    return (
      <section className="min-h-[520px] rounded-lg border border-gray-200 bg-white p-8 text-center text-sm text-gray-400 shadow-sm">
        正在加载 Trace 详情...
      </section>
    )
  }
  if (!trace) return <section className="min-h-[520px] rounded-lg border border-gray-200 bg-white shadow-sm"><EmptyState text="请选择一个 Trace 查看对话和工具调用" /></section>

  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="flex min-h-[760px] flex-col xl:flex-row">
        <div className="w-full border-b border-gray-200 xl:w-[340px] xl:border-b-0 xl:border-r">
          <div className="border-b border-gray-100 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded border border-gray-200 text-[11px] text-gray-500">T</span>
              Trace 详情
            </div>
            <div className="mt-2">
              <CopyInline value={trace.traceId} label="复制" className="text-blue-700" />
            </div>
            <div className="mt-2 grid gap-1 text-xs">
              <CopyLine label="sessionKey" value={trace.sessionKey} />
              <CopyLine label="sessionId" value={trace.sessionId} />
              <CopyLine label="归属人/Bot" value={[trace.ownerId, trace.botId].filter(Boolean).join(' / ') || null} />
            </div>
          </div>
          <div className="border-b border-gray-100 px-3 py-2">
            <input
              value={timelineSearch}
              onChange={(e) => setTimelineSearch(e.target.value)}
              placeholder="搜索调用 ID、名称、类型、输入输出或元数据"
              className="w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm outline-none focus:border-blue-300 focus:bg-white"
            />
          </div>
          <TraceTimeline
            observations={filteredObservations}
            allObservations={observations}
            selectedObservationId={selectedObservation?.observationId ?? null}
            onSelectObservation={setSelectedObservationId}
          />
        </div>

        <div className="min-w-0 flex-1">
          <ObservationDetail
            trace={trace}
            observation={selectedObservation}
            activeTab={activeTab}
            onTabChange={setActiveTab}
          />
        </div>
      </div>
    </section>
  )
}

function TraceTimeline({ observations, allObservations, selectedObservationId, onSelectObservation }: { observations: TCLogObservation[]; allObservations: TCLogObservation[]; selectedObservationId: string | null; onSelectObservation: (observationId: string) => void }) {
  const depthById = useMemo(() => {
    const byId = new Map(allObservations.map((obs) => [obs.observationId, obs]))
    const cache = new Map<string, number>()
    const depthOf = (obs: TCLogObservation, seen = new Set<string>()): number => {
      if (cache.has(obs.observationId)) return cache.get(obs.observationId) ?? 0
      const parentId = obs.parentObservationId
      if (!parentId || seen.has(parentId)) {
        cache.set(obs.observationId, 0)
        return 0
      }
      const parent = byId.get(parentId)
      const depth = parent ? Math.min(6, depthOf(parent, new Set([...seen, obs.observationId])) + 1) : 0
      cache.set(obs.observationId, depth)
      return depth
    }
    allObservations.forEach((obs) => depthOf(obs))
    return cache
  }, [allObservations])

  if (observations.length === 0) return <EmptyState text="没有调用记录" />
  return (
    <div className="max-h-[650px] overflow-auto">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-100 bg-white px-4 py-2 text-xs font-medium text-gray-500">
        <span>调用时间线</span>
        <span>{observations.length}</span>
      </div>
      {observations.map((obs) => (
        <TimelineRow
          key={obs.observationId}
          observation={obs}
          depth={depthById.get(obs.observationId) ?? 0}
          selected={selectedObservationId === obs.observationId}
          onSelect={() => onSelectObservation(obs.observationId)}
        />
      ))}
    </div>
  )
}

function TimelineRow({ observation, depth, selected, onSelect }: { observation: TCLogObservation; depth: number; selected: boolean; onSelect: () => void }) {
  const type = observation.type?.toUpperCase() || 'OBS'
  const label = observationLabel(observation)
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        onSelect()
      }}
      className={`group block w-full border-b border-gray-100 px-3 py-2 text-left hover:bg-gray-50 ${selected ? 'bg-blue-50' : 'bg-white'}`}
    >
      <div className="flex min-w-0 items-start gap-2" style={{ paddingLeft: `${depth * 16}px` }}>
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${typeDot(type)}`} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className={`truncate text-xs font-semibold ${selected ? 'text-blue-800' : 'text-gray-900'}`}>{label}</span>
            <span className="shrink-0 text-[11px] text-gray-400">{formatDuration(observation.latencyMs)}</span>
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">
            <span className={`inline-flex rounded border px-1.5 py-0.5 ${typeTone(type)}`}>{type}</span>
            {observation.model && <span className="truncate">{observation.model}</span>}
            <span>{formatTime(observation.startTimeMs)}</span>
          </div>
          <div className="mt-1">
            <CopyInline value={observation.observationId} label="复制" className="text-gray-400" />
          </div>
        </div>
      </div>
    </div>
  )
}

function ObservationDetail({ trace, observation, activeTab, onTabChange }: { trace: TCLogTrace; observation: TCLogObservation | null; activeTab: ObservationTab; onTabChange: (tab: ObservationTab) => void }) {
  if (!observation) return <EmptyState text="请选择一条调用记录" />
  const type = observation.type?.toUpperCase() || 'OBS'
  const fullJson = {
    traceId: trace.traceId,
    sessionId: trace.sessionId,
    sessionKey: trace.sessionKey,
    ...observation,
  }

  return (
    <div className="flex min-h-[760px] flex-col">
      <div className="border-b border-gray-100 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${typeTone(type)}`}>{observationLabel(observation)}</span>
              <span className="min-w-0 break-all text-sm font-semibold text-gray-900">{observation.name || observation.observationId}</span>
              <span className="font-mono text-xs text-gray-400">ID</span>
            </div>
            <div className="mt-3 grid gap-1 text-xs">
              <CopyLine label="observationId" value={observation.observationId} strong />
              <CopyLine label="parent" value={observation.parentObservationId} />
              <CopyLine label="traceId" value={trace.traceId} />
              <CopyLine label="sessionKey" value={trace.sessionKey} />
              <CopyLine label="sessionId" value={trace.sessionId} />
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-600">
          <span>{formatTime(observation.startTimeMs)}</span>
          <span className="rounded bg-gray-100 px-2 py-1">耗时：{formatDuration(observation.latencyMs)}</span>
          <span className="rounded bg-gray-100 px-2 py-1">环境：default</span>
          {observation.model && <span className="rounded bg-gray-100 px-2 py-1">模型：{observation.model}</span>}
          <span className="rounded bg-gray-100 px-2 py-1">Token：{formatNumber(observation.totalTokens)}</span>
        </div>
      </div>

      <div className="flex items-center justify-between border-b border-gray-100 px-4">
        <div className="flex gap-4">
          {(['preview'] as ObservationTab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => onTabChange(tab)}
              className={`border-b-2 px-1 py-3 text-sm font-medium ${activeTab === tab ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-600 hover:text-gray-900'}`}
            >
              预览
            </button>
          ))}
        </div>
        <div className="flex rounded-md bg-gray-100 p-0.5 text-xs">
          {(['formatted', 'json'] as ObservationTab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => onTabChange(tab)}
              className={`rounded px-2 py-1 font-medium ${activeTab === tab ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'}`}
            >
              {tab === 'json' ? 'JSON' : '格式化'}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {activeTab === 'json' ? (
          <CodeBlock value={fullJson} />
        ) : (
          <div className="space-y-4">
            <ValueSection title="输入" value={observation.input} tone="blue" defaultText="undefined" />
            <ValueSection title="输出" value={observation.output} tone="green" defaultText="undefined" />
            <ValueSection title="修正输出（Beta）" value={null} defaultText="点击添加修正输出" muted />
            <ValueSection title="元数据" value={observation.metadata ?? {
              status: observation.status,
              promptTokens: observation.promptTokens,
              completionTokens: observation.completionTokens,
              totalTokens: observation.totalTokens,
            }} />
          </div>
        )}
      </div>
    </div>
  )
}

function ValueSection({ title, value, tone, defaultText = '', muted = false }: { title: string; value: unknown; tone?: 'blue' | 'green'; defaultText?: string; muted?: boolean }) {
  const hasValue = value != null && stringifyValue(value).trim() !== ''
  const textValue = hasValue ? stringifyValue(value) : defaultText
  const toneClass = tone === 'green'
    ? 'border-green-100 bg-green-50 text-green-950'
    : tone === 'blue'
      ? 'border-blue-100 bg-blue-50 text-blue-950'
      : 'border-gray-200 bg-gray-50 text-gray-800'
  return (
    <section>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <CopyPill value={textValue} label="复制" title={`复制 ${title}`} />
      </div>
      {hasValue ? (
        <div className={`rounded-md border ${toneClass}`}>
          <CodeBlock value={value} plain />
        </div>
      ) : (
        <div className={`rounded-md border border-gray-200 px-3 py-6 text-center text-sm ${muted ? 'text-gray-400' : 'bg-gray-50 font-mono text-gray-500'}`}>
          {defaultText}
        </div>
      )}
    </section>
  )
}

function CodeBlock({ value, plain = false }: { value: unknown; plain?: boolean }) {
  return (
    <pre className={`${plain ? '' : 'rounded-md border border-gray-200 bg-gray-50'} max-h-[520px] overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-xs leading-5 text-gray-700`}>
      {stringifyValue(value) || 'undefined'}
    </pre>
  )
}

function CopyPill({ value, label = '复制', title }: { value: string | null | undefined; label?: string; title?: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  if (!value) return null
  return (
    <button
      type="button"
      onClick={async (event) => {
        event.preventDefault()
        event.stopPropagation()
        const ok = await copyText(value)
        setState(ok ? 'copied' : 'failed')
        window.setTimeout(() => setState('idle'), 1200)
      }}
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] ${
        state === 'copied'
          ? 'border-green-200 bg-green-50 text-green-700'
          : state === 'failed'
            ? 'border-red-200 bg-red-50 text-red-700'
            : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50 hover:text-gray-800'
      }`}
      title={title ?? `复制 ${label}`}
    >
      {state === 'copied' ? '已复制' : state === 'failed' ? '失败' : label}
    </button>
  )
}

function CopyInline({ value, label = '复制', className = 'text-gray-600', strong = false }: { value: string | null | undefined; label?: string; className?: string; strong?: boolean }) {
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-2 align-baseline">
      <span className={`min-w-0 break-all font-mono text-xs ${strong ? 'font-semibold' : ''} ${className}`}>{value || '-'}</span>
      <CopyPill value={value} label={label} title={`复制 ${label}`} />
    </span>
  )
}

function CopyLine({ label, value, strong = false }: { label: string; value: string | null | undefined; strong?: boolean }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-2">
      <span className="shrink-0 text-gray-400">{label}</span>
      <span className={`min-w-0 break-all font-mono ${strong ? 'text-blue-700' : 'text-gray-600'}`}>{value || '-'}</span>
      {value && (
        <button
          type="button"
          onClick={async (event) => {
            event.preventDefault()
            event.stopPropagation()
            const ok = await copyText(value)
            setState(ok ? 'copied' : 'failed')
            window.setTimeout(() => setState('idle'), 1200)
          }}
          className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] ${
            state === 'copied'
              ? 'border-green-200 bg-green-50 text-green-700'
              : state === 'failed'
                ? 'border-red-200 bg-red-50 text-red-700'
                : 'border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-800'
          }`}
          title={`复制 ${label}`}
        >
          {state === 'copied' ? '已复制' : state === 'failed' ? '失败' : '复制'}
        </button>
      )}
    </div>
  )
}

function StatusText({ status }: { status: string | null }) {
  const normalized = status?.toLowerCase() ?? ''
  const color = normalized.includes('fail') || normalized.includes('error') || normalized.includes('block')
    ? 'text-red-700 bg-red-50 border-red-200'
    : normalized.includes('success') || normalized.includes('ok') || normalized.includes('succeed')
      ? 'text-green-700 bg-green-50 border-green-200'
      : 'text-gray-700 bg-gray-50 border-gray-200'
  return <span className={`inline-flex rounded border px-2 py-0.5 text-xs ${color}`}>{status || '-'}</span>
}

function EmptyState({ text }: { text: string }) {
  return <div className="px-4 py-8 text-center text-sm text-gray-400">{text}</div>
}
