import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type EvolveTask } from '../api/client'
import { answeredInteractionHtml, platformInteractionFormStyle } from './answered-interaction-html'
import { createUnifiedDiff, GitDiffView } from '../pages/evolve/common'

type Interaction = NonNullable<EvolveTask['interactions']>[number]
type LoopInteraction = Interaction & { question: Extract<Interaction['question'], { kind: 'loop_feedback' }> }
type StandardInteraction = Interaction & { question: Exclude<Interaction['question'], { kind: 'loop_feedback' }> }
type TaskDiff = Awaited<ReturnType<typeof api.evolve.getTaskSkillDiff>>
type StructuredQuestion = Extract<Interaction['question'], { format: 'form' }>
type StructuredAnswers = Record<string, { value: string | string[]; comment?: string }>

const FORM_PAGE_SIZE = 5

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

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
  return <iframe ref={frame} title="Stage 已回答的交互表单" sandbox="allow-same-origin" srcDoc={html} style={{ height }} className="mt-3 w-full rounded-xl border border-gray-200 bg-white shadow-sm" />
}

function StructuredInteractionForm({ question, initialAnswers, readOnly, busy, onSubmit }: {
  question: StructuredQuestion
  initialAnswers?: StructuredAnswers
  readOnly: boolean
  busy: boolean
  onSubmit: (value: { answers: StructuredAnswers }) => Promise<void>
}) {
  const formId = useId()
  const [draftAnswers, setAnswers] = useState<StructuredAnswers>(() => Object.fromEntries(question.questions.map((item) => [item.id,
    initialAnswers?.[item.id] ?? { value: item.type === 'multiple_choice' ? [] : '', ...(item.type.endsWith('choice') ? { comment: '' } : {}) },
  ])))
  const answers = readOnly ? initialAnswers ?? {} : draftAnswers
  const [page, setPage] = useState(0)
  const [materialPage, setMaterialPage] = useState(0)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const contents = question.contents ?? []
  const visibleItems = question.questions.filter((item) => {
    if (!item.visibleWhen) return true
    const value = answers[item.visibleWhen.questionId]?.value
    return item.visibleWhen.operator === 'equals'
      ? value === item.visibleWhen.value
      : Array.isArray(value) && value.includes(item.visibleWhen.value)
  })
  const pageCount = Math.max(1, Math.ceil(visibleItems.length / FORM_PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const safeMaterialPage = Math.min(materialPage, Math.max(0, contents.length - 1))
  const contentPage = contents[safeMaterialPage]
  const questionPage = safePage
  const pageItems = visibleItems.slice(questionPage * FORM_PAGE_SIZE, (questionPage + 1) * FORM_PAGE_SIZE)

  const validate = (items: StructuredQuestion['questions']) => {
    const next: Record<string, string> = {}
    for (const item of items) {
      const answer = answers[item.id]
      const value = answer?.value
      if (item.type === 'single_choice') {
        if (item.required && !value) next[item.id] = '请选择一项'
        else if (value === '__other__' && !answer?.comment?.trim()) next[item.id] = '选择“其他”时请补充说明'
      } else if (item.type === 'multiple_choice') {
        const selected = Array.isArray(value) ? value : []
        const minimum = item.validation?.minSelections ?? (item.required ? 1 : 0)
        const maximum = item.validation?.maxSelections ?? Number.MAX_SAFE_INTEGER
        if (selected.length < minimum) next[item.id] = `请至少选择 ${minimum} 项`
        else if (selected.length > maximum) next[item.id] = `最多选择 ${maximum} 项`
        else if (selected.includes('__other__') && !answer?.comment?.trim()) next[item.id] = '选择“其他”时请补充说明'
      } else {
        const text = typeof value === 'string' ? value.trim() : ''
        const minimum = item.validation?.minLength ?? (item.required ? 1 : 0)
        const maximum = item.validation?.maxLength ?? 4000
        if (text.length < minimum) next[item.id] = item.required ? '请填写此项' : `至少填写 ${minimum} 个字符`
        else if (text.length > maximum) next[item.id] = `最多填写 ${maximum} 个字符`
      }
    }
    setErrors((current) => ({ ...current, ...Object.fromEntries(items.map((item) => [item.id, ''])), ...next }))
    return Object.keys(next).length === 0
  }
  const update = (id: string, value: string | string[], comment?: string) => {
    setAnswers((current) => ({ ...current, [id]: { value, ...(comment !== undefined ? { comment } : current[id]?.comment !== undefined ? { comment: current[id].comment } : {}) } }))
    setErrors((current) => ({ ...current, [id]: '' }))
  }
  const nextPage = () => {
    if (!readOnly && !validate(pageItems)) return
    setPage((current) => Math.min(pageCount - 1, current + 1))
  }
  const submit = async () => {
    if (!validate(visibleItems)) {
      const firstError = visibleItems.findIndex((item) => {
        const value = answers[item.id]?.value
        if (item.type === 'single_choice') return item.required && !value
        if (item.type === 'multiple_choice') return (Array.isArray(value) ? value.length : 0) < (item.validation?.minSelections ?? (item.required ? 1 : 0))
        return (typeof value === 'string' ? value.trim().length : 0) < (item.validation?.minLength ?? (item.required ? 1 : 0))
      })
      if (firstError >= 0) setPage(Math.floor(firstError / FORM_PAGE_SIZE))
      return
    }
    const visibleIds = new Set(visibleItems.map((item) => item.id))
    await onSubmit({ answers: Object.fromEntries(Object.entries(answers).filter(([id]) => visibleIds.has(id))) })
  }

  return <div className="mt-3 overflow-hidden rounded-xl border border-blue-100 bg-white shadow-sm">
    <div className="border-b border-gray-100 bg-gradient-to-r from-blue-50 to-white px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div><h3 className="text-base font-semibold text-gray-900">{question.title}</h3>{question.description && <p className="mt-1 text-sm leading-6 text-gray-600">{question.description}</p>}</div>
      </div>
    </div>
    <div className="space-y-5 px-5 py-5">
      {contentPage && <details className="group/materials rounded-xl border border-gray-200 bg-gray-50/50">
        <summary className="flex cursor-pointer items-center justify-between gap-3 p-4 text-sm font-semibold text-gray-900"><span>只读内容</span><span className="text-xs font-normal text-gray-500">{contents.length} 份材料 · <span className="group-open/materials:hidden">展开查看</span><span className="hidden group-open/materials:inline">收起</span></span></summary>
        <div className="border-t border-gray-200 p-5">
        <h4 className="text-base font-semibold text-gray-900">{contentPage.title}</h4>
        <div className="mt-4 max-h-[min(60vh,32rem)] space-y-3 overflow-y-auto overscroll-contain break-words pr-2 text-sm leading-7 text-gray-800 [&_a]:text-blue-600 [&_blockquote]:border-l-4 [&_blockquote]:border-gray-200 [&_blockquote]:pl-4 [&_code]:rounded [&_code]:bg-gray-100 [&_code]:px-1 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:whitespace-pre-wrap [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:p-2 [&_th]:border [&_th]:border-gray-200 [&_th]:bg-gray-100 [&_th]:p-2 [&_ul]:list-disc">
          <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{contentPage.content}</ReactMarkdown>
        </div>
        {contents.length > 1 && <div className="mt-5 flex items-center justify-between gap-3">
          <button type="button" disabled={safeMaterialPage === 0} onClick={() => setMaterialPage(safeMaterialPage - 1)} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs disabled:opacity-30">上一页</button>
          <span className="text-xs text-gray-500">第 {safeMaterialPage + 1} / {contents.length} 页</span>
          <button type="button" disabled={safeMaterialPage === contents.length - 1} onClick={() => setMaterialPage(safeMaterialPage + 1)} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs disabled:opacity-30">下一页</button>
        </div>}
        </div>
      </details>}
      <section aria-label={readOnly ? '已回答问题' : '待回答问题'} className="space-y-5 rounded-xl border border-gray-200 p-4">
        {visibleItems.length > 0 && <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold text-gray-900">{readOnly ? '已回答问题' : '待回答问题'}</h4><span className="text-xs text-gray-500">第 {safePage + 1} / {pageCount} 页</span></div>}
      {pageItems.map((item, index) => {
        const current = answers[item.id] ?? { value: item.type === 'multiple_choice' ? [] : '' }
        const choice = item.type === 'single_choice' || item.type === 'multiple_choice'
        const options = choice ? [...(item.options ?? []), { value: '__other__', label: '其他' }] : []
        return <fieldset key={item.id} className="rounded-xl border border-gray-200 bg-gray-50/50 p-4" disabled={readOnly}>
          <legend className="px-1 text-sm font-semibold text-gray-900"><span className="mr-2 text-gray-400">{questionPage * FORM_PAGE_SIZE + index + 1}.</span>{item.title}{item.required && <span className="ml-1 text-red-500">*</span>}</legend>
          {item.description && <p className="mt-1 text-xs leading-5 text-gray-500">{item.description}</p>}
          {choice ? <div className="mt-3 grid gap-2 sm:grid-cols-2">{options.map((option) => {
            const selected = item.type === 'multiple_choice'
              ? Array.isArray(current.value) && current.value.includes(option.value)
              : current.value === option.value
            return <label key={option.value} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 text-sm ${selected ? 'border-blue-400 bg-blue-50' : 'border-gray-200 bg-white'}`}>
              <input type={item.type === 'multiple_choice' ? 'checkbox' : 'radio'} name={`${formId}:${item.id}`} value={option.value} checked={selected} aria-label={option.label}
                onChange={(event) => {
                  if (item.type === 'single_choice') update(item.id, option.value)
                  else {
                    const before = Array.isArray(current.value) ? current.value : []
                    update(item.id, event.target.checked ? [...before, option.value] : before.filter((value) => value !== option.value))
                  }
                }} className="mt-0.5" />
              <span><span className="font-medium text-gray-800">{option.label}</span>{'recommended' in option && option.recommended && <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700">推荐</span>}{'description' in option && option.description && <span className="mt-0.5 block text-xs leading-5 text-gray-500">{option.description}</span>}</span>
            </label>
          })}</div> : item.type === 'long_text'
            ? <textarea aria-label={item.title} value={typeof current.value === 'string' ? current.value : ''} onChange={(event) => update(item.id, event.target.value)} placeholder={item.placeholder} className="mt-3 min-h-28 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500" />
            : <input aria-label={item.title} value={typeof current.value === 'string' ? current.value : ''} onChange={(event) => update(item.id, event.target.value)} placeholder={item.placeholder} className="mt-3 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500" />}
          {choice && <textarea aria-label={`${item.title}补充意见`} value={current.comment ?? ''} onChange={(event) => update(item.id, current.value, event.target.value)} placeholder={Array.isArray(current.value) ? current.value.includes('__other__') ? '请填写其他选项（必填）' : '补充意见（选填）' : current.value === '__other__' ? '请填写其他选项（必填）' : '补充意见（选填）'} className="mt-3 min-h-20 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500" />}
          {errors[item.id] && <p className="mt-2 text-xs text-red-600">{errors[item.id]}</p>}
        </fieldset>
      })}
    <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/70 px-5 py-4">
      {visibleItems.length > 0 ? <button type="button" disabled={safePage === 0} onClick={() => setPage((current) => Math.max(0, current - 1))} className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm text-gray-700 disabled:opacity-30">上一页</button> : <span />}
      {safePage < pageCount - 1
        ? <button type="button" onClick={nextPage} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white">下一页</button>
        : !readOnly && <button type="button" disabled={busy} onClick={() => void submit()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '提交中…' : '提交并继续'}</button>}
    </div>
      </section>
    </div>
  </div>
}

async function fileSha256(file: File): Promise<string> {
  return crypto.subtle.digest('SHA-256', await file.arrayBuffer())
    .then((value) => Array.from(new Uint8Array(value)).map((byte) => byte.toString(16).padStart(2, '0')).join(''))
}

function readableFileSize(value: unknown): string {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

function LoopFeedbackCard({ taskId, interaction, canOperate, onUpdated, closed = false }: {
  taskId: string
  interaction: LoopInteraction
  closed?: boolean
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const [feedback, setFeedback] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState<'accept' | 'continue' | ''>('')
  const [downloading, setDownloading] = useState<number | null>(null)
  const [error, setError] = useState('')
  const answered = interaction.status === 'answered'
  const savedAnswer = answered && isRecord(interaction.answer) ? interaction.answer : null
  const savedAction = savedAnswer?.action === 'accept' ? 'accept' : savedAnswer?.action === 'continue' ? 'continue' : null
  const savedFeedback = savedAction === 'continue' && isRecord(savedAnswer?.feedback) ? savedAnswer.feedback : null
  const savedText = typeof savedFeedback?.text === 'string' ? savedFeedback.text : ''
  const savedFiles = Array.isArray(savedFeedback?.files) ? savedFeedback.files.filter(isRecord) : []
  const download = async (index: number) => {
    setDownloading(index); setError('')
    try {
      const result = await api.evolve.getStageFeedbackDownloadUrl(taskId, interaction.stepId, interaction.interactionId, index)
      const anchor = document.createElement('a'); anchor.href = result.url; anchor.download = result.filename
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '获取附件失败')
    } finally { setDownloading(null) }
  }
  const submit = async (action: 'accept' | 'continue') => {
    if (!canOperate || answered) return
    if (action === 'continue' && !feedback.trim() && files.length === 0) {
      setError('请填写反馈意见或选择反馈文件')
      return
    }
    setBusy(action); setError('')
    try {
      const artifacts = []
      if (action === 'continue') {
        for (const file of files) {
          const sha256 = await fileSha256(file)
          const signed = await api.evolve.getStageFeedbackUploadUrl(
            taskId, interaction.stepId, interaction.interactionId,
            { name: file.name, size: file.size, sha256, contentType: file.type || 'application/octet-stream' },
          )
          const uploaded = await fetch(signed.url, { method: signed.method, headers: signed.headers, body: file })
          if (!uploaded.ok) throw new Error(`上传 ${file.name} 失败（${uploaded.status}）`)
          artifacts.push(signed.artifact)
        }
      }
      await api.evolve.answerStageInteraction(taskId, interaction.stepId, interaction.interactionId,
        action === 'accept' ? { action: 'accept' } : {
          action: 'continue',
          feedback: { ...(feedback.trim() ? { text: feedback.trim() } : {}), files: artifacts },
        })
      await onUpdated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提交反馈失败')
    } finally { setBusy('') }
  }
  return <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-sm font-semibold text-blue-950">{answered ? savedAction === 'accept' ? '结果确认' : '用户反馈' : '确认本轮处理结果'}</p>{!answered && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">{interaction.question.prompt}</p>}</div>
      <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-medium text-blue-700">{answered ? savedAction === 'accept' ? '已接受当前结果' : '已提交反馈，进入下一轮' : closed ? '已结束 · 未回答' : '等待确认'}</span>
    </div>
    {!answered && canOperate && <div className="mt-4 space-y-3">
      {interaction.question.accepts.text && <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="如需继续处理，请填写修改意见" className="min-h-28 w-full rounded-xl border border-blue-100 bg-white px-3 py-3 text-sm outline-none focus:border-blue-500" />}
      {interaction.question.accepts.files.length > 0 && <label className="block rounded-xl border border-dashed border-blue-200 bg-white px-4 py-3 text-sm text-gray-600">
        <span className="font-medium text-blue-700">添加反馈文件</span><span className="ml-2 text-xs text-gray-400">支持 {interaction.question.accepts.files.join('、')}，最多 10 个</span>
        <input className="mt-2 block w-full text-xs" type="file" multiple accept={interaction.question.accepts.files.join(',')} onChange={(event) => setFiles(Array.from(event.target.files ?? []).slice(0, 10))} />
        {files.length > 0 && <span className="mt-2 block text-xs text-gray-500">已选择：{files.map((file) => file.name).join('、')}</span>}
      </label>}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" disabled={Boolean(busy)} onClick={() => void submit('continue')} className="rounded-lg border border-blue-200 bg-white px-4 py-2 text-sm font-medium text-blue-700 disabled:opacity-40">{busy === 'continue' ? '正在提交…' : '提交反馈，再处理一轮'}</button>
        <button type="button" disabled={Boolean(busy)} onClick={() => void submit('accept')} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy === 'accept' ? '正在接受…' : '接受当前结果'}</button>
      </div>
    </div>}
    {answered && <div className="mt-3 rounded-xl border border-blue-100 bg-white p-4">
      <p className="text-xs font-medium text-gray-500">处理决定</p>
      <p className="mt-1 text-sm font-medium text-gray-900">{savedAction === 'accept' ? '接受当前结果' : savedAction === 'continue' ? '继续调整，再处理一轮' : '已提交'}</p>
      {savedText && <div className="mt-4 border-t border-gray-100 pt-4"><p className="text-xs font-medium text-gray-500">反馈意见</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-800">{savedText}</p></div>}
      {savedFiles.length > 0 && <div className="mt-4 border-t border-gray-100 pt-4"><p className="text-xs font-medium text-gray-500">附件</p><div className="mt-2 space-y-2">{savedFiles.map((file, index) => <div key={`${String(file.name ?? '附件')}-${index}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-2">
        <div className="min-w-0"><p className="truncate text-sm font-medium text-gray-800"><span aria-hidden="true" className="mr-1">📎</span><span>{String(file.name ?? `附件 ${index + 1}`)}</span></p><p className="mt-0.5 text-xs text-gray-400">{readableFileSize(file.size)}</p></div>
        <button type="button" disabled={downloading === index} onClick={() => void download(index)} className="text-xs font-medium text-blue-600 hover:text-blue-800 disabled:opacity-40">{downloading === index ? '获取中…' : '下载'}</button>
      </div>)}</div></div>}
      <details className="mt-4 border-t border-gray-100 pt-3"><summary className="cursor-pointer text-xs text-gray-400">技术信息</summary><pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-gray-950 p-3 text-[10px] leading-5 text-gray-200">{JSON.stringify(interaction.answer, null, 2)}</pre></details>
    </div>}
    {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
  </div>
}

function InteractionCard({ taskId, interaction, canOperate, onUpdated, closed = false }: {
  taskId: string
  interaction: StandardInteraction
  closed?: boolean
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [frameHeight, setFrameHeight] = useState(720)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const channel = useMemo(() => 'evolve-hitl:' + interaction.interactionId, [interaction.interactionId])
  const question = useMemo(() => {
    const labels: Record<string, string> = {}
    if (interaction.question.format === 'form') return { text: interaction.question.description ?? interaction.question.title, labels }
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
      if (event.source !== frameRef.current?.contentWindow || !event.data || event.data.channel !== channel) return
      if (event.data.type === 'resize' && Number.isFinite(event.data.height)) {
        setFrameHeight(Math.min(1600, Math.max(640, Math.ceil(event.data.height) + 24)))
      } else if (event.data.type === 'submit') {
        void submit(event.data.value)
      }
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [channel, interaction.question.format, interaction.status, canOperate])

  const html = interaction.question.format === 'html'
    ? '<!doctype html><html><head><meta charset="utf-8">'
      + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; img-src data:; font-src data:; connect-src \'none\'; form-action \'none\'; base-uri \'none\'">'
      + '</head><body data-evolve-form-theme="platform"><div class="evolve-form-shell">'
      + interaction.question.content + '</div>' + platformInteractionFormStyle
      + "<script>document.addEventListener('submit',function(e){e.preventDefault();var f=new FormData(e.target);var v={};f.forEach(function(x,k){if(v[k]===undefined)v[k]=x;else if(Array.isArray(v[k]))v[k].push(x);else v[k]=[v[k],x]});parent.postMessage({channel:"
      + JSON.stringify(channel)
      + ",type:'submit',value:v},'*')});function reportHeight(){parent.postMessage({channel:"
      + JSON.stringify(channel)
      + ",type:'resize',height:Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)},'*')}addEventListener('load',reportHeight);new ResizeObserver(reportHeight).observe(document.documentElement);reportHeight();</script></body></html>"
    : ''

  return <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
    <div className="flex items-center justify-between gap-3">
      <p className="text-sm font-semibold text-blue-950">{answered || closed ? '用户交互记录' : '需要补充信息'}</p>
      <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-medium text-blue-700">{answered ? '已回答' : closed ? '已结束 · 未回答' : '等待回答'}</span>
    </div>
    {interaction.question.format === 'form'
      ? <StructuredInteractionForm question={interaction.question}
          initialAnswers={response && isRecord(response.answers) ? response.answers as StructuredAnswers : undefined}
          readOnly={answered || !canOperate} busy={busy} onSubmit={submit} />
      : answeredForm
      ? <ReadOnlyInteractionForm html={answeredForm.html} />
      : !answered && canOperate && interaction.question.format === 'html'
      ? <iframe ref={frameRef} title="Stage 提交的交互表单" sandbox="allow-forms allow-scripts" srcDoc={html} style={{ height: frameHeight }} className="mt-3 w-full rounded-lg border border-amber-100 bg-white" />
      : interaction.question.format === 'text'
      ? <div className="mt-3 overflow-hidden rounded-xl border border-blue-100 bg-white shadow-sm">
        <section aria-label="交互内容" className="px-5 py-4">
          <h3 className="text-xs font-semibold text-gray-500">交互内容</h3>
          <div className="mt-3 space-y-3 break-words text-sm leading-7 text-gray-800 [&_a]:text-blue-600 [&_blockquote]:border-l-4 [&_blockquote]:border-gray-200 [&_blockquote]:pl-4 [&_code]:rounded [&_code]:bg-gray-100 [&_code]:px-1 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:whitespace-pre-wrap [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:p-2 [&_th]:border [&_th]:border-gray-200 [&_th]:bg-gray-100 [&_th]:p-2 [&_ul]:list-disc">
          <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{question.text}</ReactMarkdown>
          </div>
        </section>
        <section aria-label="用户回复" className="border-t border-gray-200 bg-gray-50/70 px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-900">{answered ? '已提交答案' : '用户回复'}</h3>
          {answered
            ? <div className="mt-3 rounded-lg border border-gray-200 bg-white p-3"><AnswerValue value={answerValue} labels={question.labels} /></div>
            : closed ? <p className="mt-3 text-sm text-gray-500">该步骤已结束，未提交回答。</p>
            : interaction.status === 'waiting' && <div className="mt-3 space-y-3">
                <textarea aria-label="用户回复" disabled={!canOperate || busy} value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="填写回答" className="min-h-28 w-full rounded-xl border border-gray-200 bg-white px-3 py-3 text-sm outline-none focus:border-blue-500 disabled:opacity-60" />
                <div className="flex justify-end"><button disabled={busy || !answer.trim() || !canOperate} onClick={() => void submit({ tag: interaction.question.tag, content: answer.trim() })} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '提交中…' : '提交并继续'}</button></div>
              </div>}
        </section>
        </div>
      : <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-800">{question.text}</p>}
    {answered && interaction.question.format === 'html' && !answeredForm && <div className="mt-3 rounded-lg bg-white p-3"><p className="mb-2 text-xs font-medium text-gray-500">已提交答案</p><AnswerValue value={answerValue} labels={question.labels} /></div>}
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
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setDiff(null); setError('')
    if (!target?.candidate?.artifact) return
    let active = true
    void api.evolve.getTaskSkillDiff(task.task_id)
      .then((value) => {
        if (!active) return
        setDiff(value); setError('')
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '候选差异加载失败')
      })
    return () => { active = false }
  }, [task.task_id, target?.assetId, target?.candidate?.artifact?.ref, target?.candidate?.artifact?.sha256])

  const unifiedDiff = useMemo(() => createUnifiedDiff(diff?.files ?? []), [diff])
  if (!target) return null
  const decide = async (decision: 'accept' | 'reject') => {
    const message = decision === 'accept'
      ? '确认将本次候选版本应用回 Host 中的原 Skill？'
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

  return <details className="group rounded-2xl border border-gray-200 bg-white shadow-sm">
    <summary className="flex cursor-pointer list-none items-start justify-between gap-3 p-5 marker:content-none">
      <div><h2 className="text-sm font-semibold text-gray-900">Skill 候选版本</h2><p className="mt-1 text-xs text-gray-500">{!target.candidate?.artifact && ['completed', 'failed', 'canceled'].includes(task.status)
        ? '任务已结束，未产生可应用的候选版本。'
        : !target.candidate?.artifact ? '候选版本尚未生成。' : `${target.name || '待进化 Skill'} · Git Diff`}</p></div>
      <span className="mt-0.5 shrink-0 text-xs font-medium text-blue-600 group-open:hidden">展开</span><span className="mt-0.5 hidden shrink-0 text-xs font-medium text-blue-600 group-open:inline">收起</span>
    </summary>
    <div className="border-t border-gray-100 px-5 pb-5 pt-4">
      {target.candidate?.artifact && task.status === 'waiting_acceptance' && canOperate && <div className="flex gap-2">
        <button disabled={Boolean(busy)} onClick={() => void decide('reject')} className="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 disabled:opacity-40">拒绝</button>
        <button disabled={Boolean(busy)} onClick={() => void decide('accept')} className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">接受并应用</button>
      </div>}
    {diff && diff.files.length > 0 && <GitDiffView content={unifiedDiff} />}
    {diff && diff.files.length === 0 && <p className="mt-4 text-sm text-gray-400">候选内容与任务开始时一致。</p>}
    {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
    </div>
  </details>
}

function interactionStepEnded(task: EvolveTask, stepId: string): boolean {
  if (['completed', 'failed', 'canceled'].includes(task.status)) return true
  const step = task.steps?.find((item) => item.stepId === stepId)
  return Boolean(step && ['succeeded', 'failed', 'canceled'].includes(step.status))
}

export default function SkillTaskRuntimePanel({ task, canOperate, onUpdated }: {
  task: EvolveTask
  canOperate: boolean
  onUpdated: () => Promise<void>
}) {
  const interactions = (task.interactions ?? []).filter((item) => item.status === 'waiting' && !interactionStepEnded(task, item.stepId))
  const showCandidate = task.task_type !== 'stage_test' && Boolean(task.config.targetSkill)
  if (!showCandidate && interactions.length === 0) return null
  return <div className="space-y-5">
    {interactions.length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">有 {interactions.length} 项信息待补充，请在对应执行步骤中回答。{interactions.map((item) => <a key={item.interactionId} href={'#step-' + item.stepId} className="ml-3 text-xs underline">前往待回答步骤</a>)}</div>}
    {showCandidate && <CandidateDecision task={task} canOperate={canOperate} onUpdated={onUpdated} />}
  </div>
}

export function StepInteractions({ task, stepId, canOperate, onUpdated, result }: {
  task: EvolveTask; stepId: string; canOperate: boolean; onUpdated: () => Promise<void>; result?: ReactNode
}) {
  const interactions = (task.interactions ?? []).filter((item) => item.stepId === stepId)
  if (!interactions.length && !result) return null
  const closed = interactionStepEnded(task, stepId)
  const feedbackIndex = interactions.findIndex(item => 'kind' in item.question && item.question.kind === 'loop_feedback')
  const resultIndex = feedbackIndex < 0 ? interactions.length : feedbackIndex
  const cards = interactions.map((item) => 'kind' in item.question && item.question.kind === 'loop_feedback'
    ? <LoopFeedbackCard key={item.interactionId} taskId={task.task_id} interaction={item as LoopInteraction} closed={closed} canOperate={canOperate && !closed} onUpdated={onUpdated} />
    : <InteractionCard key={item.interactionId} taskId={task.task_id} interaction={item as StandardInteraction} closed={closed} canOperate={canOperate && !closed} onUpdated={onUpdated} />)
  return <div className="mt-4 space-y-3">{cards.slice(0, resultIndex)}{result}{cards.slice(resultIndex)}</div>
}
