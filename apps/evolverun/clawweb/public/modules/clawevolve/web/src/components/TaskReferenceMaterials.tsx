import { useEffect, useRef, useState } from 'react'
import './TaskReferenceMaterials.css'
import { referenceMaterialsApi, type MaterialView } from '../api/reference-materials'
import type { TaskReferenceMaterialsState } from '../hooks/useTaskReferenceMaterials'

function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB` }
const buttonClass = 'rounded-lg border border-input px-3 py-1.5 text-sm text-foreground disabled:opacity-50'

export function TaskReferenceMaterialsField({ state, disabled = false }: { state: TaskReferenceMaterialsState; disabled?: boolean }) {
  const files = useRef<HTMLInputElement>(null), directory = useRef<HTMLInputElement>(null)
  const [directorySupported, setDirectorySupported] = useState(false)
  useEffect(() => { setDirectorySupported('webkitdirectory' in document.createElement('input')) }, [])
  const unavailable = !state.options?.available
  const roots = [...new Set(state.files.filter(item => item.path.includes('/')).map(item => item.path.split('/')[0]))]
  const locked = disabled || state.busy
  return <section className="task-reference-materials space-y-3 rounded-xl border border-input bg-background p-4" aria-label="参考材料">
    <div><h2 className="text-sm font-semibold text-foreground">参考材料 <span className="font-normal text-muted-foreground">（可选）</span></h2>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">上传业务规范、案例或示例代码，Agent 将结合本次目标参考材料。目录结构会保留，材料中的脚本不会自动执行。</p></div>
    {!state.opened ? <button type="button" className={buttonClass} disabled={disabled} onClick={state.open}>添加参考材料</button>
      : !state.options ? <p className="text-xs text-muted-foreground">{state.error ? '暂时无法读取上传能力。' : '正在检查上传能力…'}<button type="button" className="ml-2 underline" onClick={state.retryOptions}>重试</button></p>
      : unavailable ? <p className="text-xs text-muted-foreground">当前参考材料上传不可用。<button type="button" className="ml-2 underline" onClick={state.retryOptions}>重新检查</button></p> : <>
      <p className="text-xs text-muted-foreground">最多 {state.options!.maxFiles} 个文件，单文件 {size(state.options!.maxFileBytes)}，总计 {size(state.options!.maxTotalBytes)}。具体格式的读取取决于执行环境。</p>
      <input ref={files} type="file" multiple hidden aria-label="选择参考文件" onChange={event => { if (event.target.files) state.add(event.target.files); event.target.value = '' }} />
      <input ref={directory} type="file" multiple hidden aria-label="选择参考目录" {...{ webkitdirectory: '' }} onChange={event => { if (event.target.files) state.add(event.target.files); event.target.value = '' }} />
      <div className="flex flex-wrap gap-2"><button type="button" disabled={locked} className={buttonClass} onClick={() => files.current?.click()}>添加文件</button>
        <button type="button" disabled={locked || !directorySupported} className={buttonClass} onClick={() => directory.current?.click()}>添加目录</button></div>
      {!directorySupported && <p className="text-xs text-muted-foreground">当前浏览器不支持目录选择，可以添加文件。</p>}
    </>}
    {state.files.length > 0 && <>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{state.files.length} 个文件 · {size(state.files.reduce((sum, item) => sum + item.file.size, 0))}</span>
        {roots.map(root => <button type="button" key={root} disabled={locked} onClick={() => state.remove(root)} className="underline">移除目录 {root}</button>)}</div>
      <ul className="max-h-52 overflow-auto text-xs">{state.files.map(item => <li key={item.path} className="flex items-start justify-between gap-3 border-b border-input py-2 last:border-0"><span className="min-w-0 break-all font-mono">{item.path}</span><span className="flex shrink-0 items-center gap-2 text-muted-foreground">{size(item.file.size)}<button type="button" disabled={locked} aria-label={`移除 ${item.path}`} onClick={() => state.remove(item.path)}>移除</button></span></li>)}</ul>
      <div className="flex items-center gap-3"><button type="button" className={buttonClass} disabled={locked || Boolean(state.uploaded)} onClick={() => void state.upload()}>{state.busy ? '正在打包上传…' : state.uploaded ? '上传完成' : '上传材料'}</button>
        <span className="text-xs text-muted-foreground" role="status">{state.uploaded ? '创建任务后固定使用这份材料。' : '请上传完成后再创建任务。'}</span></div>
    </>}
    {state.error && <p role="alert" className="text-xs text-destructive">{state.error}</p>}
  </section>
}

export function TaskReferenceMaterialsDetail({ taskId }: { taskId: string }) {
  const [value, setValue] = useState<MaterialView | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [downloading, setDownloading] = useState(false)
  useEffect(() => {
    let active = true
    setError(''); setValue(null)
    void referenceMaterialsApi.view(taskId).then(result => { if (active) setValue(result) })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '材料读取失败') })
    return () => { active = false }
  }, [taskId, attempt])
  async function download() {
    if (downloading) return
    setDownloading(true); setError('')
    try {
      // Re-sign on every user action, including when the detail page was left open.
      const latest = await referenceMaterialsApi.view(taskId)
      const anchor = document.createElement('a'); anchor.href = latest.download.url; anchor.download = `${taskId}-reference-materials.zip`
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '材料下载失败') }
    finally { setDownloading(false) }
  }
  return <section aria-label="任务参考材料" className="task-reference-materials rounded-2xl border border-input bg-background p-5 shadow-sm">
    <h2 className="text-sm font-semibold text-foreground">参考材料</h2>
    {value ? <><p className="mt-2 text-xs text-muted-foreground">{value.files.length} 个文件 · {size(value.totalBytes)} · 创建时保存的原始材料</p>
      <ul className="mt-3 max-h-60 space-y-2 overflow-auto text-xs">{value.files.map(file => <li key={file.path} className="break-all font-mono">{file.path} <span className="text-muted-foreground">{size(file.size)}</span></li>)}</ul>
      <button type="button" disabled={downloading} className={`${buttonClass} mt-3`} onClick={() => void download()}>{downloading ? '准备下载…' : '下载材料包'}</button></> : !error && <p role="status" className="mt-2 text-xs text-muted-foreground">正在读取材料…</p>}
    {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}<button type="button" className="ml-2 underline" onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
  </section>
}
