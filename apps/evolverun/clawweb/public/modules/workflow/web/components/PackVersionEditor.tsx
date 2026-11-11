import { useEffect, useRef, useState } from 'react'
import PackFileCodeEditor from './PackFileCodeEditor'

/** The parent owns the complete candidate; this component neither loads nor persists it. */
export type PackVersionFile = { path: string; contentBase64: string; mode: '100644' | '100755' }
export type PackVersionEditorProps = {
  files: PackVersionFile[]
  onChange: (files: PackVersionFile[]) => void
  /**
   * Initially false. The parent must synchronously track this signal and block save,
   * deploy, version switches, and unmounting while true. False follows onChange on
   * success; errors, external candidate changes, and unmount also release it.
   */
  onUploadStateChange: (uploading: boolean) => void
  /** Candidate provenance shown in the information column; the editor never persists it. */
  versionSource?: PackVersionEditorVersionSource
  /** Overall save state is owned by the parent, not inferred from individual edits. */
  saveStatus?: 'clean' | 'dirty' | 'saving'
}

export type PackVersionEditorVersionSource = {
  version: 'current' | 'draft'
  packId: string
  baseRef: string
  baseCommit: string
}

export const MAX_PACK_VERSION_FILE_BYTES = 1024 * 1024
const MAX_PATH_BYTES = 1024

function encodeBytes(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binary)
}

function editableText(base64: string): string | null {
  // Reject malformed or non-canonical input rather than silently changing it.
  if (base64.length > Math.ceil(MAX_PACK_VERSION_FILE_BYTES / 3) * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64)) return null
  try {
    const binary = atob(base64)
    if (binary.length > MAX_PACK_VERSION_FILE_BYTES) return null
    const bytes = Uint8Array.from(binary, char => char.charCodeAt(0))
    if (encodeBytes(bytes) !== base64) return null
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
    // Textareas normalize CR and cannot safely represent control characters.
    if (/[\x00-\x08\x0b-\x1f\x7f-\x9f]/u.test(text)) return null
    return encodeBytes(new TextEncoder().encode(text)) === base64 ? text : null
  } catch {
    return null
  }
}

function validPath(path: string, files: PackVersionFile[]): string | null {
  if (!path || /^[A-Za-z]:/.test(path) || new TextEncoder().encode(path).length > MAX_PATH_BYTES ||
      /[\\\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/u.test(path) ||
      path.split('/').some(part => !part || part === '.' || part === '..')) {
    return '请输入有效的相对路径（不能包含空路径段、点路径或控制字符）。'
  }
  if (files.some(file => file.path === path || file.path.startsWith(`${path}/`) || path.startsWith(`${file.path}/`))) {
    return '该路径已存在，或与现有文件路径冲突。'
  }
  return null
}

function sameFiles(a: PackVersionFile[], b: PackVersionFile[]): boolean {
  return a.length === b.length && a.every((file, index) =>
    file.path === b[index].path && file.mode === b[index].mode && file.contentBase64 === b[index].contentBase64)
}

const UNKNOWN_SOURCE: PackVersionEditorVersionSource = { version: 'current', packId: '未提供', baseRef: '未提供', baseCommit: '未提供' }

function byteLength(contentBase64: string): number | null {
  try { return atob(contentBase64).length } catch { return null }
}

function groupedFiles(files: PackVersionFile[], query: string): Array<{ name: string; files: PackVersionFile[] }> {
  const normalized = query.trim().toLowerCase()
  const groups: Array<{ name: string; files: PackVersionFile[] }> = []
  for (const file of files) {
    if (normalized && !file.path.toLowerCase().includes(normalized)) continue
    const name = file.path.includes('/') ? `${file.path.split('/')[0]}/` : 'Pack 根目录'
    const group = groups.find(item => item.name === name)
    if (group) group.files.push(file)
    else groups.push({ name, files: [file] })
  }
  return groups
}

export default function PackVersionEditor({ files, onChange, onUploadStateChange, versionSource = UNKNOWN_SOURCE, saveStatus = 'clean' }: PackVersionEditorProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [newPath, setNewPath] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [newMode, setNewMode] = useState<PackVersionFile['mode']>('100644')
  const [uploading, setUploading] = useState(false)
  const currentFiles = useRef(files)
  const uploadStateCallback = useRef(onUploadStateChange)
  const changeCallback = useRef(onChange)
  const pending = useRef<{ reader: FileReader; snapshot: PackVersionFile[] } | null>(null)
  const uploadInput = useRef<HTMLInputElement>(null)
  currentFiles.current = files
  uploadStateCallback.current = onUploadStateChange
  changeCallback.current = onChange

  function finishUpload(reader: FileReader, mounted = true) {
    if (pending.current?.reader !== reader) return
    pending.current = null
    if (mounted) setUploading(false)
    uploadStateCallback.current(false)
  }

  useEffect(() => {
    const active = pending.current
    if (active && !sameFiles(active.snapshot, files)) {
      finishUpload(active.reader)
      active.reader.abort()
      setError('版本文件已更改，上传已取消；请重新选择文件。')
    }
  }, [files])
  useEffect(() => () => {
    const active = pending.current
    if (active) {
      finishUpload(active.reader, false)
      active.reader.abort()
    }
  }, [])

  const groups = groupedFiles(files, search)
  const selected = files.find(file => file.path === selectedPath) ?? null
  const text = selected ? editableText(selected.contentBase64) : null
  const selectedBytes = selected ? byteLength(selected.contentBase64) : null
  function change(next: PackVersionFile[]) {
    setError('')
    onChange(next)
  }

  function addFile() {
    const pathError = validPath(newPath, files)
    if (pathError) { setError(pathError); return }
    change([...files, { path: newPath, contentBase64: '', mode: newMode }])
    setSelectedPath(newPath)
    setNewPath('')
    setNewMode('100644')
  }

  function updateContent(contentBase64: string) {
    if (!selected) return
    change(files.map(file => file === selected ? { ...file, contentBase64 } : file))
  }

  function editText(value: string) {
    if (/[\x00-\x08\x0b-\x1f\x7f-\x9f]/u.test(value)) {
      setError('文本包含无法安全编辑的控制字符，未应用更改。')
      return
    }
    if (/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/u.test(value)) {
      setError('文本包含无法编码的字符，未应用更改。')
      return
    }
    const bytes = new TextEncoder().encode(value)
    if (bytes.length > MAX_PACK_VERSION_FILE_BYTES) {
      setError('文件超过 1 MiB 限制，未应用更改。')
      return
    }
    updateContent(encodeBytes(bytes))
  }

  function upload(file: File | undefined) {
    if (!file || !selected || pending.current) return
    if (file.size > MAX_PACK_VERSION_FILE_BYTES) {
      setError('文件超过 1 MiB 限制，未应用替换。')
      return
    }
    setError('')
    const snapshot = files.map(item => ({ ...item }))
    const path = selected.path
    const reader = new FileReader()
    pending.current = { reader, snapshot }
    setUploading(true)
    // Notify the parent before starting any asynchronous work.
    uploadStateCallback.current(true)
    reader.onerror = () => {
      if (pending.current?.reader !== reader) return
      setError('读取文件失败，未应用替换。')
      finishUpload(reader)
    }
    reader.onabort = () => {
      if (pending.current?.reader !== reader) return
      setError('上传已取消，未应用替换。')
      finishUpload(reader)
    }
    reader.onload = () => {
      if (pending.current?.reader !== reader) return
      if (!sameFiles(snapshot, currentFiles.current)) {
        setError('版本文件已更改，上传已取消；请重新选择文件。')
        finishUpload(reader)
        return
      }
      if (!(reader.result instanceof ArrayBuffer) || reader.result.byteLength > MAX_PACK_VERSION_FILE_BYTES) {
        setError('读取文件失败或文件超过 1 MiB 限制，未应用替换。')
        finishUpload(reader)
        return
      }
      try {
        const contentBase64 = encodeBytes(new Uint8Array(reader.result))
        changeCallback.current(currentFiles.current.map(item => item.path === path ? { ...item, contentBase64 } : item))
      } finally {
        // The parent receives the candidate before it is told it can save again.
        finishUpload(reader)
      }
    }
    try {
      reader.readAsArrayBuffer(file)
    } catch {
      setError('读取文件失败，未应用替换。')
      finishUpload(reader)
    }
  }

  const saveStateText = uploading
    ? '正在读取上传文件…'
    : saveStatus === 'saving'
      ? '正在保存…'
      : saveStatus === 'dirty'
        ? '有未保存更改'
        : '与已保存内容一致'

  return <section aria-label="版本文件编辑器" className="space-y-2">
    <p className="text-[11px] leading-relaxed text-muted-foreground">编辑提供的版本文件；更改仅交由上层处理，不会自动保存或部署。单个文件编辑或上传上限为 1 MiB；已有较大文件保持原样。</p>
    {uploading && <p role="status" className="text-[11px]">正在读取上传文件，请等待完成后再保存或切换。</p>}
    {error && <p role="alert" className="text-[11px] text-destructive">{error}</p>}
    <div className="flex min-h-[520px] flex-col overflow-hidden rounded-lg border border-border bg-card xl:flex-row">
      <aside aria-label="Pack 文件导航" className="min-w-0 shrink-0 border-b border-border bg-muted/30 px-3 py-[15px] xl:w-[235px] xl:border-b-0 xl:border-r">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-[13px] font-bold text-foreground">Pack 文件</h3>
          <span className="text-[10px] text-muted-foreground">相对 Pack 根目录</span>
        </div>
        <input type="search" aria-label="搜索 Pack 文件" placeholder="搜索文件名..." className="h-[33px] w-full rounded-[7px] border border-input bg-background px-2.5 text-[11px] text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30" value={search} disabled={uploading} onChange={event => setSearch(event.target.value)} />
        <div className="mt-2.5 flex gap-1.5">
          <button type="button" aria-label="添加空文件" disabled={uploading} className="flex min-h-[31px] flex-1 items-center justify-center rounded-[7px] border border-input bg-background px-2.5 text-[11px] font-semibold text-foreground disabled:opacity-40" onClick={addFile}>新建</button>
          <button type="button" disabled={uploading || !selected} className="flex min-h-[31px] flex-1 items-center justify-center rounded-[7px] border border-input bg-background px-2.5 text-[11px] font-semibold text-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-primary disabled:opacity-40" onClick={() => uploadInput.current?.click()}>上传</button>
        </div>
        <div className="mt-2.5 grid gap-1.5">
          <input aria-label="新文件相对路径" placeholder="新文件相对路径..." className="h-[31px] min-w-0 rounded-[7px] border border-input bg-background px-2.5 text-[11px] text-foreground placeholder:text-muted-foreground" value={newPath} disabled={uploading} onChange={event => setNewPath(event.target.value)} />
          <select aria-label="新文件模式" className="h-[27px] rounded-[7px] border border-input bg-background px-2 text-[11px] text-foreground" value={newMode} disabled={uploading} onChange={event => setNewMode(event.target.value as PackVersionFile['mode'])}>
            <option value="100644">100644（普通文件）</option>
            <option value="100755">100755（可执行文件）</option>
          </select>
        </div>
        <div className="mt-2">
          {groups.length === 0
            ? <p className="mt-4 px-2.5 text-[10px] text-muted-foreground">{files.length === 0 ? '没有文件。' : '没有匹配的文件。'}</p>
            : <ul aria-label="版本文件列表">
              {groups.map(group => <li key={group.name}>
                <div className="mx-1 mb-1.5 mt-4 flex items-center justify-between gap-2 text-[11px] font-semibold text-muted-foreground">
                  <span>{group.name}</span>
                  <span className="font-mono">({group.files.length})</span>
                </div>
                <ul>
                  {group.files.map(file => <li key={file.path}>
                    <button type="button" aria-label={file.path} aria-pressed={selected?.path === file.path} disabled={uploading} onClick={() => { setError(''); setSelectedPath(file.path) }} className="group flex w-full cursor-pointer items-center gap-2 rounded-[7px] border border-input/60 bg-background px-2 py-2 text-left text-[11px] font-medium text-muted-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 aria-pressed:border-primary/50 aria-pressed:bg-primary/10 aria-pressed:text-primary disabled:opacity-40">
                      <svg aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground group-hover:text-primary" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 16 16">
                        <path d="M4 3.5h3.5l1 1H12A1.5 1.5 0 0 1 13.5 6v6A1.5 1.5 0 0 1 12 13.5H4A1.5 1.5 0 0 1 2.5 12V5A1.5 1.5 0 0 1 4 3.5Z" />
                      </svg>
                      <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{file.path.split('/').pop()}</span>
                      <svg aria-hidden="true" className="h-3 w-3 shrink-0 text-muted-foreground/60 group-hover:text-primary/80" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 16 16"><path d="m6 4 4 4-4 4" /></svg>
                    </button>
                  </li>)}
                </ul>
              </li>)}
            </ul>}
        </div>
      </aside>
      <section aria-label="所选文件" className="flex min-w-0 flex-1 flex-col bg-card">
        {selected ? <>
          <header className="flex min-h-[49px] flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <span aria-hidden="true" className="text-[13px] text-muted-foreground">▤</span>
              <strong className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[12px] font-semibold text-foreground">{selected.path.split('/').pop()}</strong>
              <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] text-muted-foreground">{selected.path}</span>
            </div>
            <div className="flex items-center gap-2">
              <select aria-label="所选文件模式" className="h-[27px] rounded-[7px] border border-input bg-background px-2 text-[11px] text-foreground" value={selected.mode} disabled={uploading} onChange={event => change(files.map(file => file === selected ? { ...file, mode: event.target.value as PackVersionFile['mode'] } : file))}>
                <option value="100644">100644（普通文件）</option>
                <option value="100755">100755（可执行文件）</option>
              </select>
              <button type="button" disabled={uploading} className="flex min-h-[31px] items-center rounded-[7px] border border-input bg-background px-2.5 text-[11px] font-semibold text-destructive hover:bg-destructive/5 disabled:opacity-40" onClick={() => { change(files.filter(file => file !== selected)); setSelectedPath(null) }}>删除所选文件</button>
            </div>
          </header>
          <div className="flex min-h-[430px] flex-1 flex-col bg-background">
            {text === null
              ? <div className="p-4">
                <p className="text-[11px] leading-[1.8] text-muted-foreground">此文件不是可安全编辑的 UTF-8 文本，或超过编辑大小限制；原内容保持不变，可上传文件替换。</p>
              </div>
              : <PackFileCodeEditor path={selected.path} value={text} disabled={uploading} onChange={editText} />}
          </div>
          <footer className="flex items-center justify-between border-t border-border px-4 py-1.5">
            <span className="font-mono text-[10px] text-muted-foreground">{selectedBytes === null ? '—' : `${selectedBytes} B`} · UTF-8</span>
            <span className="font-mono text-[10px] text-muted-foreground">{saveStateText}</span>
          </footer>
          <input ref={uploadInput} type="file" aria-label="上传文件替换所选文件" className="sr-only" disabled={uploading} onChange={event => { upload(event.target.files?.[0]); event.target.value = '' }} />
        </> : <p className="p-4 text-[11px] text-muted-foreground">请选择或新建文件。</p>}
      </section>
      <aside aria-label="文件信息" className="min-w-0 shrink-0 border-t border-border px-4 py-4 xl:w-[275px] xl:border-l xl:border-t-0">
        <h3 className="mb-3 text-[13px] font-semibold text-foreground">文件信息</h3>
        <div className="border-b border-border/70 py-3">
          <p className="mb-2 text-[10px] font-bold text-muted-foreground">文件详情</p>
          <dl className="grid gap-1">
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">文件名</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{selected?.path.split('/').pop() ?? '—'}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">文件路径</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{selected?.path ?? '—'}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">模式</dt><dd className="font-mono text-[10px] font-semibold text-foreground">{selected?.mode ?? '—'}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">大小</dt><dd className="font-mono text-[10px] font-semibold text-foreground">{selectedBytes === null ? '—' : `${selectedBytes} B`}</dd></div>
          </dl>
        </div>
        <div className="border-b border-border/70 py-3">
          <p className="mb-2 text-[10px] font-bold text-muted-foreground">版本来源</p>
          <dl className="grid gap-1">
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">版本</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{versionSource.version === 'current' ? '当前版（生效 tag）' : '草稿版（wf）'}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">Pack</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{versionSource.packId}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">分支 / Tag</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{versionSource.baseRef}</dd></div>
            <div className="flex items-center justify-between gap-2"><dt className="text-[10px] text-muted-foreground">Commit</dt><dd className="max-w-[65%] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[10px] font-semibold text-foreground">{versionSource.baseCommit}</dd></div>
          </dl>
        </div>
        <div className="py-3">
          <p className="mb-2 text-[10px] font-bold text-muted-foreground">保存状态</p>
          <p className="font-mono text-[10px] text-muted-foreground">{saveStateText}</p>
          <p className="mt-3 rounded-lg border border-primary/15 bg-primary/5 p-2.5 text-[10px] leading-[1.7] text-muted-foreground">保存与部署由上层统一处理；刷新或关闭页面会丢失未保存更改。</p>
        </div>
      </aside>
    </div>
  </section>
}
