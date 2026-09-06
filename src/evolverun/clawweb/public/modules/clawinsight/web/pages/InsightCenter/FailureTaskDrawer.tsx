import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { insightApi } from '../../api/insight'
import type { FailureTaskDetail, FailureTaskIndex, TimelineBlockDetail, TimelineBlockSummary } from '../../types/insight'
import { InsightIcon, ErrorPanel, LoadingPanel } from './InsightUi'
import { completionText, failureClassText, formatDateTime, formatDuration, formatMessageRange, jsonText } from './utils'

type Props = { task: FailureTaskIndex; onClose: () => void; layer?: 'default' | 'admin' }
type TextMode = 'rendered' | 'raw'

function markdownProps<T extends { node?: unknown }>(props: T): Omit<T, 'node'> {
  const { node, ...rest } = props
  void node
  return rest
}

const markdownComponents: Components = {
  h1: (props) => <h1 className="mb-3 mt-5 border-b border-gray-200 pb-2 text-xl font-semibold text-gray-950 first:mt-0" {...markdownProps(props)} />,
  h2: (props) => <h2 className="mb-2 mt-5 text-lg font-semibold text-gray-900 first:mt-0" {...markdownProps(props)} />,
  h3: (props) => <h3 className="mb-2 mt-4 text-base font-semibold text-gray-900 first:mt-0" {...markdownProps(props)} />,
  p: (props) => <p className="my-2 leading-7 text-gray-800" {...markdownProps(props)} />,
  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5 text-gray-800" {...markdownProps(props)} />,
  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5 text-gray-800" {...markdownProps(props)} />,
  li: (props) => <li className="leading-7" {...markdownProps(props)} />,
  blockquote: (props) => <blockquote className="my-3 border-l-4 border-blue-200 bg-blue-50/60 px-4 py-2 text-gray-700" {...markdownProps(props)} />,
  table: (props) => <div className="my-3 overflow-x-auto rounded-xl border border-gray-200"><table className="min-w-full divide-y divide-gray-200 text-sm" {...markdownProps(props)} /></div>,
  thead: (props) => <thead className="bg-gray-50 text-left text-xs font-semibold text-gray-500" {...markdownProps(props)} />,
  th: (props) => <th className="px-3 py-2" {...markdownProps(props)} />,
  td: (props) => <td className="border-t border-gray-100 px-3 py-2 align-top text-gray-700" {...markdownProps(props)} />,
  pre: (props) => <pre className="my-3 overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100" {...markdownProps(props)} />,
  code: ({ className, ...props }) => <code className={className ?? 'rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-800'} {...markdownProps(props)} />,
  hr: (props) => <hr className="my-4 border-gray-200" {...markdownProps(props)} />,
  a: (props) => <a className="font-medium text-blue-600 hover:text-blue-700" target="_blank" rel="noreferrer" {...markdownProps(props)} />,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function looksLikeMarkdown(text: string): boolean {
  const sample = text.slice(0, 8000)
  return /(^|\n)#{1,6}\s+\S/.test(sample)
    || /(^|\n)```/.test(sample)
    || /(^|\n)\s*[-*+]\s+\S/.test(sample)
    || /(^|\n)\s*\d+\.\s+\S/.test(sample)
    || /(^|\n)>\s+\S/.test(sample)
    || /(^|\n)\|.+\|/.test(sample)
    || /^---\s*\n/.test(sample)
}

function looksLikeStructuredPreview(text: string): boolean {
  const trimmed = text.trim()
  return trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.includes('```json') || /"[\w-]+"\s*:/.test(trimmed)
}

function MarkdownBody({ text }: { text: string }) {
  return <div className="text-sm"><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{text}</ReactMarkdown></div>
}

function TextContent({ text }: { text: string }) {
  const canRenderMarkdown = looksLikeMarkdown(text)
  const [mode, setMode] = useState<TextMode>(canRenderMarkdown ? 'rendered' : 'raw')

  if (!canRenderMarkdown) {
    return <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6 text-gray-800">{text}</pre>
  }

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-gray-600"><InsightIcon name="clipboard" />Markdown 内容</div>
        <div className="inline-flex rounded-lg border border-gray-200 bg-white p-0.5 text-[11px]">
          <button onClick={() => setMode('rendered')} className={`rounded-md px-2 py-1 ${mode === 'rendered' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-50'}`}>渲染</button>
          <button onClick={() => setMode('raw')} className={`rounded-md px-2 py-1 ${mode === 'raw' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-50'}`}>原文</button>
        </div>
      </div>
      <div className="p-4">
        {mode === 'rendered'
          ? <MarkdownBody text={text} />
          : <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 text-gray-800">{text}</pre>}
      </div>
    </div>
  )
}

function ToolResultContent({ value }: { value: unknown }) {
  // Tool results are evidence, not prose: keep the original payload untouched.
  // In particular, do not run JSON through Markdown or reformat a JSON string.
  const text = typeof value === 'string' ? value : jsonText(value)
  return <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">{text}</pre>
}

function ContentValue({ value }: { value: unknown }) {
  if (typeof value === 'string') return <TextContent text={value} />
  if (Array.isArray(value)) {
    return <div className="space-y-3">{value.map((item, index) => {
      if (isRecord(item) && typeof item.text === 'string') {
        return <div key={index} className="rounded-xl border border-gray-200 bg-white p-4"><TextContent text={item.text} /></div>
      }
      if (isRecord(item) && typeof item.name === 'string') {
        return (
          <div key={index} className="overflow-hidden rounded-xl border border-slate-700 bg-slate-950 text-slate-100">
            <div className="flex items-center gap-2 border-b border-slate-700 px-3 py-2 text-xs font-medium text-sky-300"><InsightIcon name="code" />工具调用 · {item.name}</div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-5">{jsonText(item.arguments ?? item.partialArgs ?? item)}</pre>
          </div>
        )
      }
      return <pre key={index} className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">{jsonText(item)}</pre>
    })}</div>
  }
  return <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">{jsonText(value)}</pre>
}

function BlockTone({ block, children }: { block: TimelineBlockSummary; children: ReactNode }) {
  const style = block.kind === 'user_message'
    ? 'ml-12 border-blue-200 bg-blue-50/70'
    : block.kind === 'assistant_message'
      ? 'mr-8 border-gray-200 bg-white'
      : block.kind === 'judge_result'
        ? 'border-violet-200 bg-violet-50/70'
        : 'mx-4 border-slate-200 bg-slate-50'
  return <article className={`rounded-xl border p-4 shadow-sm ${style}`}>{children}</article>
}

function blockKindText(block: TimelineBlockSummary): string {
  if (block.kind === 'user_message') return '用户消息'
  if (block.kind === 'assistant_message') return 'Agent 回复'
  if (block.kind === 'judge_result') return 'LLM Judge'
  return '执行轨迹'
}

function PreviewCard({ block }: { block: TimelineBlockSummary }) {
  const structured = looksLikeStructuredPreview(block.preview)
  const tone = block.kind === 'user_message'
    ? 'border-blue-100 bg-white/80 text-blue-950'
    : block.kind === 'assistant_message'
      ? 'border-gray-100 bg-gray-50/80 text-gray-800'
      : block.kind === 'judge_result'
        ? 'border-violet-100 bg-white/80 text-violet-950'
        : 'border-slate-200 bg-white text-slate-800'
  return (
    <div className={`mt-3 overflow-hidden rounded-xl border ${tone}`}>
      <div className="flex gap-2 px-3 py-2.5">
        <span className="mt-0.5 shrink-0 text-gray-400"><InsightIcon name={block.kind === 'judge_result' ? 'judge' : block.kind === 'agent_execution' ? 'code' : 'message'} /></span>
        <p className={`line-clamp-4 whitespace-pre-wrap break-words ${structured ? 'font-mono text-xs leading-5' : 'text-sm leading-6'}`}>{block.preview || '该节点没有文本摘要'}</p>
      </div>
    </div>
  )
}

function FullscreenReader({ block, onClose }: { block: TimelineBlockDetail; onClose: () => void }) {
  const rawOnly = block.kind === 'assistant_message' && block.raw
  return (
    <div className="fixed inset-0 z-[90] flex flex-col bg-gray-950/50 p-4 backdrop-blur-sm sm:p-8" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4"><div><p className="text-xs text-gray-400">{blockKindText(block)} · {block.blockId}</p><h3 className="mt-1 text-base font-semibold text-gray-900">{block.title}</h3></div><button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button></div>
        <div className="min-h-0 flex-1 overflow-auto p-5">{rawOnly ? <ToolResultContent value={block.raw} /> : <ContentValue value={block.content} />}</div>
      </div>
    </div>
  )
}

export default function FailureTaskDrawer({ task, onClose, layer = 'default' }: Props) {
  const [visible, setVisible] = useState(false)
  const [activeTaskIndex, setActiveTaskIndex] = useState(task.taskIndex)
  const [detail, setDetail] = useState<FailureTaskDetail | null>(null)
  const [blocks, setBlocks] = useState<TimelineBlockSummary[]>([])
  const [blockDetails, setBlockDetails] = useState<Record<string, TimelineBlockDetail>>({})
  const [openBlocks, setOpenBlocks] = useState<Set<string>>(new Set())
  const [loadingBlock, setLoadingBlock] = useState('')
  const [fullscreenBlock, setFullscreenBlock] = useState<TimelineBlockDetail | null>(null)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  const anchorTaskIndex = task.taskIndex
  const timelineParams = useMemo(() => ({ anchorTaskIndex, ownerUserId: task.ownerUserId }), [anchorTaskIndex, task.ownerUserId])

  const handleClose = useCallback(() => {
    setVisible(false)
    window.setTimeout(onClose, 180)
  }, [onClose])

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true))
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') handleClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleClose])

  useEffect(() => {
    setActiveTaskIndex(task.taskIndex)
  }, [task.sessionId, task.taskIndex])

  useEffect(() => {
    let active = true
    setDetail(null); setBlocks([]); setBlockDetails({}); setOpenBlocks(new Set()); setError('')
    Promise.all([
      insightApi.failureTaskDetail(task.sessionId, activeTaskIndex, timelineParams),
      insightApi.timeline(task.sessionId, activeTaskIndex, { ...timelineParams, all: true }),
    ]).then(([detailResult, timelineResult]) => {
      if (!active) return
      setDetail(detailResult)
      setBlocks(timelineResult.items as TimelineBlockSummary[])
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : '失败任务详情加载失败')
    })
    return () => { active = false }
  }, [task.sessionId, activeTaskIndex, timelineParams, reloadKey])

  const activeTask = detail?.task ?? { ...task, taskIndex: activeTaskIndex }
  const activeSessionTask = detail?.sessionTasks.find((sessionTask) => sessionTask.taskIndex === activeTaskIndex)
  const activeMessageRange = activeSessionTask?.messageRange ?? detail?.judge.message_range ?? [0, 0]
  const backdropLayer = layer === 'admin' ? 'z-[75]' : 'z-40'
  const drawerLayer = layer === 'admin' ? 'z-[80]' : 'z-50'

  const judgeEntries = useMemo(() => {
    if (!detail) return []
    const judge = detail.judge
    return [
      ['任务判定', completionText[judge.is_complete]],
      ['失败分类', failureClassText[String(judge.task_failure_class ?? activeTask.failureClass)] ?? String(judge.task_failure_class ?? activeTask.failureClass)],
      ['人工介入', String(judge.human_intervention_level ?? '—')],
      ['人工轮次', String(judge.human_turn_count ?? 0)],
    ]
  }, [detail, activeTask.failureClass])

  const toggleBlock = async (block: TimelineBlockSummary) => {
    if (openBlocks.has(block.blockId)) {
      setOpenBlocks((current) => { const next = new Set(current); next.delete(block.blockId); return next })
      return
    }
    setOpenBlocks((current) => new Set(current).add(block.blockId))
    if (blockDetails[block.blockId]) return
    setLoadingBlock(block.blockId)
    try {
      const result = await insightApi.timeline(task.sessionId, activeTaskIndex, { ...timelineParams, blockId: block.blockId, pageSize: 1 })
      const loaded = result.items[0] as TimelineBlockDetail | undefined
      if (loaded) setBlockDetails((current) => ({ ...current, [block.blockId]: loaded }))
    } catch (reason) {
      setBlockDetails((current) => ({ ...current, [block.blockId]: { ...block, content: reason instanceof Error ? reason.message : '节点加载失败', raw: null } }))
    } finally { setLoadingBlock('') }
  }

  return (
    <>
      <button aria-label="关闭失败任务详情" className={`fixed inset-0 cursor-default bg-gray-950/25 backdrop-blur-[1px] ${backdropLayer}`} onClick={handleClose} />
      <aside role="dialog" aria-modal="true" aria-label="失败任务详情" className={`fixed right-0 top-0 flex h-full w-full max-w-[980px] transform flex-col bg-gray-50 shadow-2xl transition-transform duration-200 ${drawerLayer} ${visible ? 'translate-x-0' : 'translate-x-full'}`}>
        <header className="shrink-0 border-b border-gray-200 bg-white px-5 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2 text-xs"><span className="rounded-full bg-red-50 px-2.5 py-1 font-medium text-red-700">失败任务</span><span className="text-gray-400">Session 内 Task {activeTaskIndex}</span>{activeTaskIndex !== anchorTaskIndex && <span className="rounded-full bg-gray-100 px-2.5 py-1 font-medium text-gray-500">来自同 Session 证据</span>}</div><h2 className="mt-2 truncate text-lg font-semibold text-gray-950" title={activeSessionTask?.taskDescription ?? activeTask.taskDescription}>{activeSessionTask?.taskDescription ?? activeTask.taskDescription}</h2><p className="mt-1 truncate font-mono text-[11px] text-gray-400">{task.sessionId}{detail ? ` · message ${formatMessageRange(activeMessageRange)}` : ''}</p></div>
            <button onClick={handleClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600"><InsightIcon name="close" /></button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {error ? <ErrorPanel message={error} onRetry={() => setReloadKey((value) => value + 1)} /> : !detail ? <LoadingPanel text="正在读取 Session 证据…" /> : <div className="space-y-5 p-5 sm:p-6">
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-4"><div className="max-w-3xl"><p className="text-xs font-medium text-violet-600">LLM Judge 判定</p><p className="mt-2 text-sm leading-6 text-gray-800">{String(detail.judge.reasoning ?? activeTask.judgeReasonSummary ?? '暂无判定说明')}</p></div><span className="rounded-full bg-orange-50 px-3 py-1.5 text-xs font-medium text-orange-700">{failureClassText[activeTask.failureClass] ?? activeTask.failureClass}</span></div>
              <div className="mt-4 grid gap-3 border-t border-gray-100 pt-4 sm:grid-cols-4">{judgeEntries.map(([label, value]) => <div key={label}><p className="text-[11px] text-gray-400">{label}</p><p className="mt-1 text-sm font-medium text-gray-800">{value}</p></div>)}</div>
              {detail.judge.human_intervention_reasoning != null && <div className="mt-4 rounded-xl bg-gray-50 px-4 py-3"><p className="text-[11px] font-medium text-gray-500">人工介入判定说明</p><p className="mt-1 text-xs leading-5 text-gray-600">{String(detail.judge.human_intervention_reasoning)}</p></div>}
            </section>

            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold text-gray-900">Session 内 Task 切分</h3><p className="mt-1 text-xs text-gray-400">整个 Session 已拉取，可点击 Task 切换查看对应消息区间、执行轨迹与 Judge 判定。</p></div><span className="text-xs text-gray-400">共 {detail.sessionTasks.length} 个</span></div>
              <div className="mt-4 flex gap-3 overflow-x-auto pb-2">{detail.sessionTasks.map((sessionTask) => {
                const current = sessionTask.taskIndex === activeTaskIndex
                const failed = sessionTask.isComplete !== 1
                return (
                  <button key={sessionTask.taskIndex} onClick={() => setActiveTaskIndex(sessionTask.taskIndex)} className={`w-60 shrink-0 rounded-xl border p-3 text-left transition ${current ? 'border-blue-300 bg-blue-50 ring-2 ring-blue-500/10' : 'border-gray-200 bg-gray-50 hover:border-blue-200 hover:bg-blue-50/40'}`}>
                    <div className="flex items-center justify-between"><span className={`text-xs font-semibold ${current ? 'text-blue-700' : 'text-gray-600'}`}>Task {sessionTask.taskIndex}</span><span className={`rounded-full px-2 py-0.5 text-[10px] ${failed ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>{completionText[sessionTask.isComplete]}</span></div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-700">{sessionTask.taskDescription}</p>
                    <div className="mt-2 flex items-center justify-between gap-2"><p className="font-mono text-[10px] text-gray-400">message {formatMessageRange(sessionTask.messageRange)}</p>{sessionTask.taskIndex === anchorTaskIndex && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[9px] font-medium text-red-600">入口</span>}</div>
                  </button>
                )
              })}</div>
            </section>

            <section className="rounded-2xl border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-100 px-5 py-4"><h3 className="text-sm font-semibold text-gray-900">时间线</h3><p className="mt-1 text-xs text-gray-400">当前 Task 的全部消息、执行轨迹和 Judge 判定；用户消息中的 Sender 元信息已隐藏。</p></div>
              <div className="space-y-3 p-4 sm:p-5">{blocks.map((block) => {
                const expanded = openBlocks.has(block.blockId)
                const loaded = blockDetails[block.blockId]
                return <BlockTone key={block.blockId} block={block}>
                  <button className="w-full text-left" onClick={() => void toggleBlock(block)}>
                    <div className="flex items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-gray-800">{blockKindText(block)}</span><span className="font-mono text-[10px] text-gray-400">{block.blockId}</span>{block.visibility === 'internal' && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[9px] text-slate-600">执行过程</span>}</div><p className="mt-1 text-xs text-gray-500">{block.title}{block.timestamp != null ? ` · ${formatDateTime(block.timestamp)}` : ''}</p></div><span className={`mt-1 shrink-0 text-gray-400 transition ${expanded ? 'rotate-90' : ''}`}><InsightIcon name="arrow" /></span></div>
                    {!expanded && <PreviewCard block={block} />}
                    <div className="mt-2 flex items-center gap-3 text-[10px] text-gray-400"><span>{block.charCount.toLocaleString()} 字符</span><span>{expanded ? '收起' : block.expandable ? '展开完整内容' : '查看节点内容'}</span></div>
                  </button>
                  {expanded && <div className="mt-4 border-t border-gray-200/80 pt-4">{loadingBlock === block.blockId && !loaded ? <div className="py-5 text-center text-xs text-gray-400">正在读取完整节点…</div> : loaded ? <>{loaded.kind === 'assistant_message' && loaded.raw
                    ? <ToolResultContent value={loaded.raw} />
                    : <div className="max-h-[520px] overflow-auto rounded-xl border border-gray-200 bg-white p-4">
                      {loaded.kind === 'agent_execution'
                        ? <ToolResultContent value={loaded.content} />
                        : <ContentValue value={loaded.content} />}
                    </div>}<div className="mt-3 flex justify-end"><button onClick={() => setFullscreenBlock(loaded)} className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:text-blue-700"><InsightIcon name="external" />全屏阅读</button></div></> : null}</div>}
                </BlockTone>
              })}

              </div>
            </section>

            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"><h3 className="text-sm font-semibold text-gray-900">Session 元信息</h3><div className="mt-4 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3"><Info label="Bot" value={detail.session.botName || detail.session.botId} /><Info label="Bot ID" value={detail.session.botId} mono /><Info label="开始时间" value={formatDateTime(detail.session.startTime)} /><Info label="结束时间" value={formatDateTime(detail.session.endTime)} /><Info label="执行时长" value={formatDuration(detail.session.durationSeconds)} /><Info label="消息数量" value={`${detail.session.messageCount} 条`} /><Info label="任务来源" value={detail.session.isCron ? '定时任务' : '用户发起'} /><Info label="数据水位" value={formatDateTime(detail.dataAsOf)} /><Info label="Evidence 版本" value={detail.evidence.schemaVersion} mono /></div></section>
          </div>}
        </div>
      </aside>
      {fullscreenBlock && <FullscreenReader block={fullscreenBlock} onClose={() => setFullscreenBlock(null)} />}
    </>
  )
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div><p className="text-[11px] text-gray-400">{label}</p><p className={`mt-1 break-words text-xs text-gray-700 ${mono ? 'font-mono' : ''}`}>{value}</p></div>
}
