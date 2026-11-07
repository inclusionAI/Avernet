import { useEffect, useRef, useState } from 'react'

export interface ScriptFile {
  path: string
  content: string | null
  savedContent: string | null
  isNew: boolean
}

interface CommonProps {
  files: ScriptFile[]
  selectedPath: string | null
  onSelect: (path: string) => void
  loading: boolean
  error: string | null
  onRetry: () => void
}

type WriteCallbacks = {
  onChange: (path: string, content: string) => void
  onAdd: (path: string, content: string) => void
  onDelete: (path: string) => void
}

export type ScriptFilesPanelProps = CommonProps & (
  | ({ readOnly: false } & WriteCallbacks)
  | ({ readOnly: true } & Partial<WriteCallbacks>)
)

const MAX_SIZE = 1024 * 1024
const validPath = (path: string) => {
  const parts = path.split('/')
  return parts.length >= 2 && parts[0] === 'scripts' &&
    parts.slice(1).every(part => /^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$/.test(part) && part !== '.' && part !== '..') &&
    /\.(py|js|ts|sh|bash)$/.test(path)
}

export default function ScriptFilesPanel(props: ScriptFilesPanelProps) {
  const { files, selectedPath, onSelect, readOnly, loading, error, onRetry } = props
  const [path, setPath] = useState('scripts/')
  const [message, setMessage] = useState<string | null>(null)
  const [confirmPath, setConfirmPath] = useState<string | null>(null)
  const latest = useRef(props)
  latest.current = props
  const uploadId = useRef(0)
  useEffect(() => () => { uploadId.current++ }, [])
  const selected = files.find(file => file.path === selectedPath)
  const disabled = readOnly || loading || error !== null

  function validatePath(candidate: string, existing: ScriptFile[]) {
    if (!validPath(candidate)) return '路径必须位于 scripts/ 下，使用安全相对路径及支持的脚本扩展名。'
    if (existing.some(file => file.path === candidate)) return '路径已存在。'
    return null
  }

  function add() {
    if (props.readOnly || disabled) return
    const problem = validatePath(path, files)
    if (problem) { setMessage(problem); return }
    setMessage(null)
    props.onAdd(path, '')
  }

  function upload(file: File | undefined) {
    const id = ++uploadId.current
    if (!file || disabled) return
    const candidate = `scripts/${file.name}`
    const problem = validatePath(candidate, files)
    if (problem) { setMessage(problem); return }
    if (file.size > MAX_SIZE) { setMessage('文件不能超过 1 MB。'); return }
    setMessage(null)
    const reader = new FileReader()
    reader.onerror = () => { if (id === uploadId.current) setMessage('读取文件失败。') }
    reader.onload = () => {
      if (id !== uploadId.current) return
      const current = latest.current
      if (current.readOnly || current.loading || current.error !== null) return
      const pathError = validatePath(candidate, current.files)
      if (pathError) { setMessage(pathError); return }
      let content: string
      try {
        if (!(reader.result instanceof ArrayBuffer)) throw new Error('Invalid file')
        content = new TextDecoder('utf-8', { fatal: true }).decode(reader.result)
        if (content.includes('\0')) throw new Error('Not text')
      } catch { setMessage('文件必须是有效的 UTF-8 文本。'); return }
      current.onAdd(candidate, content)
    }
    reader.readAsArrayBuffer(file)
  }

  function changeContent(file: ScriptFile, value: string) {
    if (props.readOnly || disabled) return
    if (new TextEncoder().encode(value).length > MAX_SIZE) { setMessage('脚本不能超过 1 MB。'); return }
    if (value.includes('\0')) { setMessage('脚本必须是 UTF-8 文本。'); return }
    setMessage(null)
    props.onChange(file.path, value)
  }

  return <section aria-label="脚本文件" className="overflow-hidden rounded-lg border border-border bg-background text-sm">
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
      <h2 className="font-semibold">脚本文件</h2>
      {readOnly && <span className="rounded bg-muted px-2 py-1 text-muted-foreground">只读模式</span>}
    </header>
    {loading && <p role="status" className="px-4 py-2 text-muted-foreground">正在加载脚本…</p>}
    {error !== null && <div role="alert" className="px-4 py-2 text-destructive"><p>{error}</p><button type="button" onClick={onRetry} className="underline">重试</button></div>}
    {message && <p role="alert" className="px-4 py-2 text-destructive">{message}</p>}
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(12rem,16rem)_minmax(0,1fr)_minmax(12rem,16rem)]">
      <section aria-label="文件树" className="min-w-0 border-b border-border bg-muted/30 p-4 lg:border-b-0 lg:border-r">
        <h3 className="font-semibold">Pack 文件</h3>
        <p className="mt-1 text-xs text-muted-foreground">相对 Pack 根目录</p>
        {!readOnly && <div className="mt-4 space-y-2">
          <label className="block text-xs">新脚本路径 <input aria-label="新脚本路径" className="mt-1 w-full rounded border border-input bg-background px-2 py-1" value={path} disabled={disabled} onChange={event => setPath(event.target.value)} /></label>
          <button type="button" className="rounded border border-input bg-background px-2 py-1" disabled={disabled} onClick={add}>新建脚本</button>
          <label className="block text-xs">上传脚本 <input aria-label="上传脚本" className="mt-1 block w-full text-xs" type="file" accept=".py,.js,.ts,.sh,.bash" disabled={disabled} onChange={event => { upload(event.target.files?.[0]); event.target.value = '' }} /></label>
        </div>}
        <h4 className="mt-4 text-xs font-medium text-muted-foreground">scripts / · {files.length} 个文件</h4>
        <ul className="mt-2 space-y-1">
          {files.map(file => <li key={file.path}>
            <button type="button" aria-pressed={file.path === selectedPath} onClick={() => { setConfirmPath(null); onSelect(file.path) }} className={`w-full break-all rounded px-2 py-2 text-left ${file.path === selectedPath ? 'bg-primary/10 text-primary' : 'hover:bg-muted'}`}>
              {file.path} {file.content === null ? <span className="text-xs text-muted-foreground">已删除</span> : file.isNew ? <span className="text-xs text-muted-foreground">新增</span> : file.content !== file.savedContent ? <span className="text-xs text-muted-foreground">已修改</span> : null}
            </button>
          </li>)}
        </ul>
        {!loading && !error && files.length === 0 && <p className="mt-2 text-muted-foreground">没有脚本文件。</p>}
      </section>
      <section aria-label="脚本编辑区" className="min-w-0 border-b border-border lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0"><h3 className="break-all font-semibold">{selected ? selected.path.split('/').pop() : '未选择文件'}</h3><p className="break-all text-xs text-muted-foreground">{selected?.path}</p></div>
          {!readOnly && selected && selected.content !== null && <button type="button" className="rounded border border-input px-2 py-1 text-destructive" disabled={disabled} onClick={() => setConfirmPath(selected.path)}>删除脚本</button>}
        </div>
        {selected && selected.content !== null && <>
          {confirmPath === selected.path && !props.readOnly && !disabled && <div role="group" aria-label="确认删除脚本" className="space-x-2 border-b border-border p-3">
            <p>确定删除 {selected.path}？</p>
            <button type="button" className="text-destructive" onClick={() => { if (!props.readOnly) props.onDelete(selected.path); setConfirmPath(null) }}>确认删除</button>
            <button type="button" onClick={() => setConfirmPath(null)}>取消</button>
          </div>}
          <textarea aria-label="脚本内容" spellCheck={false} className="block min-h-80 w-full resize-y bg-background p-4 font-mono text-sm outline-none" value={selected.content} readOnly={readOnly} disabled={loading || error !== null} onChange={props.readOnly ? undefined : event => changeContent(selected, event.target.value)} />
          <div className="border-t border-border px-4 py-2 text-xs text-muted-foreground">UTF-8{readOnly ? ' · 只读' : ''}</div>
        </>}
        {selected?.content === null && <p className="p-4 text-muted-foreground">该脚本已删除。</p>}
        {!selected && <p className="p-4 text-muted-foreground">请选择脚本文件。</p>}
      </section>
      <section aria-label="文件信息" className="min-w-0 bg-muted/30 p-4">
        <h3 className="font-semibold">文件信息</h3>
        <dl className="mt-4 space-y-3 text-xs">
          <div><dt className="text-muted-foreground">文件路径</dt><dd className="mt-1 break-all">{selected?.path ?? '—'}</dd></div>
          <div><dt className="text-muted-foreground">访问方式</dt><dd className="mt-1">{readOnly ? '只读查看' : '工作区编辑'}</dd></div>
          <div><dt className="text-muted-foreground">格式要求</dt><dd className="mt-1">UTF-8 · .py / .js / .ts / .sh / .bash</dd></div>
        </dl>
        <p className="mt-4 text-xs text-muted-foreground">仅支持 scripts/ 下的安全相对路径，最大 1 MB；服务端会再次校验。</p>
      </section>
    </div>
  </section>
}
