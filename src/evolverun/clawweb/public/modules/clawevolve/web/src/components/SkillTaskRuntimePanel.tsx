import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type EvolveTask } from '../api/client'
import { answeredInteractionHtml } from './answered-interaction-html'

type Interaction = NonNullable<EvolveTask['interactions']>[number]
type TaskDiff = Awaited<ReturnType<typeof api.evolve.getTaskSkillDiff>>

function AnswerValue({ value, labels }: { value: unknown; labels: Record<string, string> }) {
  if (Array.isArray(value)) return <ul className="list-inside list-disc space-y-1">{value.map((item, index) => <li key={index}><AnswerValue value={item} labels={labels} /></li>)}</ul>
  if (value && typeof value === 'object') return <dl className="space-y-2">{Object.entries(value).map(([key, item]) => <div key={key}><dt className="text-xs text-gray-500">{labels[key] || key}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-800"><AnswerValue value={item} labels={labels} /></dd></div>)}</dl>
  return <span>{value === true ? '是' : value === false ? '否' : value == null ? '（未填写）' : String(value)}</span>
}

function ReadOnlyInteractionForm({ html }: { html: string }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(448)
  useEffect(() => {
    const element = frame.current
    if (!element) return
    let observer: ResizeObserver | undefined
    const measure = () => {
      observer?.disconnect()
      const body = element.contentDocument?.body
      if (!body) return
      const resize = () => setHeight(Math.min(1200, Math.max(192, Math.ceil(body.getBoundingClientRect().height) + 32)))
      resize()
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(resize)
        observer.observe(body)
      }
    }
    element.addEventListener('load', measure)
    measure()
    return () => { element.removeEventListener('load', measure); observer?.disconnect() }
  }, [html])
  // Same-origin only lets this trusted parent measure the static document; scripts and forms stay forbidden.
  return <iframe ref={frame} title="Stage 已回答的交互表单" sandbox="allow-same-origin" srcDoc={html} style={{ height }} className="mt-3 w-full rounded-lg border border-amber-100 bg-white" />
}

function InteractionCard({ taskId, interaction, canOperate, onUpdated }: {
  taskId: string
  interaction: Interaction
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const frameRef = useRef<HTMLIFrameElement>(null)
  const channel = useMemo(() => 'evolve-hitl:' + interaction.interactionId, [interaction.interactionId])
  const question = useMemo(() => {
    const labels: Record<string, string> = {}
    if (interaction.question.format !== 'html') return { text: interaction.question.content, labels }
    const template = document.createElement('template')
    template.innerHTML = interaction.question.content
    const doc = template.content
    doc.querySelectorAll('input,textarea,select').forEach((field) => {
      const name = field.getAttribute('name')
      const label = Array.from(doc.querySelectorAll('label')).find((item) => item.htmlFor && item.htmlFor === field.id) ?? field.closest('label')
      if (name) labels[name] = field.getAttribute('aria-label') || label?.textContent?.trim() || name
    })
    doc.querySelectorAll('script,style,input,textarea,select,button').forEach((item) => item.remove())
    return { text: doc.textContent?.trim() || '请根据表单补充信息', labels }
  }, [interaction.question.format, interaction.question.content])
  const answered = interaction.status === 'answered'
  const response = interaction.answer && typeof interaction.answer === 'object' && !Array.isArray(interaction.answer)
    ? interaction.answer as Record<string, unknown> : null
  const answerValue = response && 'tag' in response ? response.fields ?? response.content ?? interaction.answer : interaction.answer
  const answeredForm = useMemo(() => answered && interaction.question.format === 'html'
    ? answeredInteractionHtml(interaction.question.content, answerValue) : null,
  [answered, interaction.question.format, interaction.question.content, answerValue])

  const submit = async (value: unknown) => {
    if (!canOperate || interaction.status !== 'waiting') return
    setBusy(true); setError('')
    try {
      await api.evolve.answerStageInteraction(taskId, interaction.stepId, interaction.interactionId, value)
      await onUpdated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提交回答失败')
    } finally { setBusy(false) }
  }

  useEffect(() => {
    if (interaction.question.format !== 'html' || interaction.status !== 'waiting' || !canOperate) return
    const receive = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow
        || !event.data || event.data.channel !== channel || event.data.type !== 'submit') return
      void submit(event.data.value)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [channel, interaction.question.format, interaction.status, canOperate])

  const html = interaction.question.format === 'html'
    ? '<!doctype html><html><head><meta charset="utf-8">'
      + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; img-src data:; font-src data:; connect-src \'none\'; form-action \'none\'; base-uri \'none\'">'
      + '</head><body>'
      + interaction.question.content
      + "<script>document.addEventListener('submit',function(e){e.preventDefault();var f=new FormData(e.target);var v={};f.forEach(function(x,k){if(v[k]===undefined)v[k]=x;else if(Array.isArray(v[k]))v[k].push(x);else v[k]=[v[k],x]});parent.postMessage({channel:"
      + JSON.stringify(channel)
      + ",type:'submit',value:v},'*')});</script></body></html>"
    : ''

  return <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
    <div className="flex items-center justify-between gap-3">
      <p className="text-xs font-semibold text-amber-900">{answered ? '用户交互记录' : '需要补充信息'}</p>
      <span className="text-[10px] text-amber-700">{interaction.status === 'waiting' ? '等待回答' : '已回答'}</span>
    </div>
    {answeredForm
      ? <ReadOnlyInteractionForm html={answeredForm.html} />
      : !answered && canOperate && interaction.question.format === 'html'
      ? <iframe ref={frameRef} title="Stage 提交的交互表单" sandbox="allow-forms allow-scripts" srcDoc={html} className="mt-3 min-h-48 w-full rounded-lg border border-amber-100 bg-white" />
      : <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-800">{question.text}</p>}
    {interaction.status === 'waiting' && interaction.question.format === 'text' && <div className="mt-3 flex gap-2">
      <textarea value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="填写回答" className="min-h-20 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500" />
      <button disabled={busy || !answer.trim() || !canOperate} onClick={() => void submit({ tag: interaction.question.tag, content: answer.trim() })} className="self-end rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '提交中…' : '提交并继续'}</button>
    </div>}
    {answered && (!answeredForm || answeredForm.remainingAnswer != null) && <div className="mt-3 rounded-lg bg-white p-3"><p className="mb-2 text-xs font-medium text-gray-500">已提交答案</p><AnswerValue value={answeredForm ? answeredForm.remainingAnswer : answerValue} labels={question.labels} /></div>}
    {answered && <details className="mt-3"><summary className="cursor-pointer text-xs text-gray-500">交互技术详情</summary><pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-gray-950 p-3 text-xs text-gray-200">{JSON.stringify({ question: interaction.question, answer: interaction.answer }, null, 2)}</pre></details>}
    {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
  </div>
}

function CandidateDecision({ task, canOperate, onUpdated }: {
  task: EvolveTask
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const target = task.config.targetSkill as {
    name?: string
    assetId?: string
    candidate?: { artifact?: { ref?: string; sha256?: string } }
  } | undefined
  const [diff, setDiff] = useState<TaskDiff | null>(null)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setDiff(null); setSelected(''); setError('')
    if (!target?.candidate?.artifact) return
    let active = true
    void api.evolve.getTaskSkillDiff(task.task_id)
      .then((value) => {
        if (!active) return
        setDiff(value); setSelected(value.files[0]?.path ?? ''); setError('')
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '候选差异加载失败')
      })
    return () => { active = false }
  }, [task.task_id, target?.assetId, target?.candidate?.artifact?.ref, target?.candidate?.artifact?.sha256])

  if (!target) return null
  const file = diff?.files.find((item) => item.path === selected)
  const decide = async (decision: 'accept' | 'reject') => {
    const message = decision === 'accept'
      ? '确认将本次候选版本应用回 OCB 中的原 Skill？'
      : '确认拒绝本次候选版本？原 Skill 不会被修改。'
    if (!window.confirm(message)) return
    setBusy(decision); setError('')
    try {
      await api.evolve.decideSkillVersion(task.task_id, decision)
      await onUpdated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提交决定失败')
    } finally { setBusy('') }
  }

  return <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-sm font-semibold text-gray-900">Skill 候选版本</h2><p className="mt-1 text-xs text-gray-500">{target.name || '待进化 Skill'} · 与本次任务开始时冻结的内容对比</p></div>
      {target.candidate?.artifact && task.status === 'waiting_acceptance' && canOperate && <div className="flex gap-2">
        <button disabled={Boolean(busy)} onClick={() => void decide('reject')} className="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 disabled:opacity-40">拒绝</button>
        <button disabled={Boolean(busy)} onClick={() => void decide('accept')} className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">接受并应用</button>
      </div>}
    </div>
    {!target.candidate?.artifact && <p className="mt-4 text-sm text-gray-400">{['completed', 'failed', 'canceled'].includes(task.status)
      ? '任务已结束，未产生可应用的候选版本。' : '候选版本尚未生成。'}</p>}
    {diff && <><div className="mt-4 flex flex-wrap gap-2">
      {diff.files.map((item) => <button key={item.path} onClick={() => setSelected(item.path)} className={'rounded-md border px-2.5 py-1.5 text-xs ' + (selected === item.path ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-gray-200 text-gray-600')}>{item.path} · {item.change === 'added' ? '新增' : item.change === 'deleted' ? '删除' : '修改'}</button>)}
    </div>
    {file && <div className="mt-3 grid gap-3 lg:grid-cols-2">
      <div><p className="mb-1 text-[10px] font-medium text-gray-400">任务开始时</p><pre className="max-h-80 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] leading-5 text-gray-200">{file.before ?? '（无）'}</pre></div>
      <div><p className="mb-1 text-[10px] font-medium text-gray-400">候选版本</p><pre className="max-h-80 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] leading-5 text-gray-200">{file.after ?? '（已删除）'}</pre></div>
    </div>}</>}
    {diff && diff.files.length === 0 && <p className="mt-4 text-sm text-gray-400">候选内容与任务开始时一致。</p>}
    {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
  </section>
}

export default function SkillTaskRuntimePanel({ task, canOperate, onUpdated }: {
  task: EvolveTask
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const interactions = (task.interactions ?? []).filter((item) => item.status === 'waiting')
  const showCandidate = task.task_type !== 'stage_test' && Boolean(task.config.targetSkill)
  if (!showCandidate && interactions.length === 0) return null
  return <div className="space-y-5">
    {interactions.length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">有 {interactions.length} 项信息待补充，请在对应执行步骤中回答。{interactions.map((item) => <a key={item.interactionId} href={'#step-' + item.stepId} className="ml-3 text-xs underline">前往待回答步骤</a>)}</div>}
    {showCandidate && <CandidateDecision task={task} canOperate={canOperate} onUpdated={onUpdated} />}
  </div>
}

export function StepInteractions({ task, stepId, canOperate, onUpdated }: {
  task: EvolveTask; stepId: string; canOperate: boolean; onUpdated: () => Promise<void>
}) {
  const interactions = (task.interactions ?? []).filter((item) => item.stepId === stepId)
  if (!interactions.length) return null
  return <div className="mt-4 space-y-3">{interactions.map((item) => <InteractionCard key={item.interactionId} taskId={task.task_id} interaction={item} canOperate={canOperate} onUpdated={onUpdated} />)}</div>
}
