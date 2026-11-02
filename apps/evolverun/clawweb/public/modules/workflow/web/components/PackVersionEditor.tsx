import { useEffect, useRef, useState } from 'react'

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

  return <section aria-label="版本文件编辑器" className="space-y-4">
    <p className="text-sm text-muted-foreground">编辑提供的版本文件；更改仅交由上层处理，不会自动保存或部署。单个文件编辑或上传上限为 1 MiB；已有较大文件保持原样。</p>
    {uploading && <p role="status" className="text-sm">正在读取上传文件，请等待完成后再保存或切换。</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="grid gap-4 xl:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)_minmax(14rem,18rem)]">
      <aside aria-label="Pack 文件导航" className="min-w-0 rounded-lg border border-border bg-card p-4">
        <div className="space-y-1">
          <h3 className="font-semibold">Pack 文件</h3>
          <p className="text-xs text-muted-foreground">相对 Pack 根目录</p>
        </div>
        <label className="mt-3 block text-xs">搜索 Pack 文件
          <input type="search" className="mt-1 w-full rounded border border-input bg-background px-2 py-1" value={search} disabled={uploading} onChange={event => setSearch(event.target.value)} />
        </label>
        <div className="mt-4 space-y-2">
          <label className="grid gap-1 text-xs">新文件相对路径
            <input className="rounded border border-input bg-background px-2 py-1" value={newPath} disabled={uploading} onChange={event => setNewPath(event.target.value)} />
          </label>
          <label className="grid gap-1 text-xs">新文件模式
            <select className="rounded border border-input bg-background px-2 py-1" value={newMode} disabled={uploading} onChange={event => setNewMode(event.target.value as PackVersionFile['mode'])}>
              <option value="100644">100644（普通文件）</option>
              <option value="100755">100755（可执行文件）</option>
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" aria-label="添加空文件" disabled={uploading} className="rounded border border-input bg-background px-3 py-1" onClick={addFile}>新建</button>
            <button type="button" disabled={uploading || !selected} className="rounded border border-input bg-background px-3 py-1" onClick={() => uploadInput.current?.click()}>上传</button>
          </div>
        </div>
        <div className="mt-4">
          {groups.length === 0
            ? <p className="text-sm text-muted-foreground">{files.length === 0 ? '没有文件。' : '没有匹配的文件。'}</p>
            : <ul aria-label="版本文件列表" className="space-y-4">
              {groups.map(group => <li key={group.name}>
                <p className="mb-1 text-xs font-medium text-muted-foreground">{group.name} · {group.files.length}</p>
                <ul className="space-y-1">
                  {group.files.map(file => <li key={file.path}>
                    <button type="button" aria-pressed={selected?.path === file.path} disabled={uploading} onClick={() => { setError(''); setSelectedPath(file.path) }} className="w-full break-all rounded px-2 py-1 text-left hover:bg-muted aria-pressed:bg-primary aria-pressed:text-primary-foreground">{file.path}</button>
                  </li>)}
                </ul>
              </li>)}
            </ul>}
        </div>
      </aside>
      <section aria-label="所选文件" className="flex min-w-0 flex-col rounded-lg border border-border bg-card">
        {selected ? <>
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <h3 className="break-all text-base font-semibold">{selected.path.split('/').pop()}</h3>
              <p className="break-all text-xs text-muted-foreground">路径：{selected.path}</p>
            </div>
            <button type="button" disabled={uploading} className="rounded border border-input px-3 py-1 text-destructive" onClick={() => { change(files.filter(file => file !== selected)); setSelectedPath(null) }}>删除所选文件</button>
          </header>
          <div className="flex-1 space-y-3 p-4">
            <label className="grid gap-1 text-sm">所选文件模式
              <select className="w-fit rounded border border-input bg-background px-2 py-1" value={selected.mode} disabled={uploading} onChange={event => change(files.map(file => file === selected ? { ...file, mode: event.target.value as PackVersionFile['mode'] } : file))}>
                <option value="100644">100644（普通文件）</option>
                <option value="100755">100755（可执行文件）</option>
              </select>
            </label>
            {text === null
              ? <p className="text-sm">此文件不是可安全编辑的 UTF-8 文本，或超过编辑大小限制；原内容保持不变，可上传文件替换。</p>
              : <label className="grid gap-1 text-sm">文件内容
                <textarea className="min-h-72 w-full flex-1 rounded border border-input bg-background p-2 font-mono" value={text} disabled={uploading} onChange={event => editText(event.target.value)} />
              </label>}
          </div>
          <footer className="border-t border-border px-4 py-3">
            <label className="grid gap-1 text-sm">上传文件替换所选文件
              <input ref={uploadInput} type="file" disabled={uploading} onChange={event => { upload(event.target.files?.[0]); event.target.value = '' }} />
            </label>
          </footer>
        </> : <p className="p-4 text-sm text-muted-foreground">请选择或新建文件。</p>}
      </section>
      <aside aria-label="文件信息" className="min-w-0 rounded-lg border border-border bg-card p-4">
        <h3 className="font-semibold">文件信息</h3>
        <dl className="mt-3 space-y-3 text-xs">
          <div><dt className="text-muted-foreground">文件名</dt><dd className="mt-1 break-all">{selected?.path.split('/').pop() ?? '—'}</dd></div>
          <div><dt className="text-muted-foreground">文件路径</dt><dd className="mt-1 break-all">{selected?.path ?? '—'}</dd></div>
          <div><dt className="text-muted-foreground">模式</dt><dd className="mt-1">{selected?.mode ?? '—'}</dd></div>
          <div><dt className="text-muted-foreground">大小</dt><dd className="mt-1">{selectedBytes === null ? '—' : `${selectedBytes} B`}</dd></div>
        </dl>
        <h4 className="mt-5 text-xs font-medium text-muted-foreground">版本来源</h4>
        <dl className="mt-2 space-y-3 text-xs">
          <div><dt className="text-muted-foreground">版本</dt><dd className="mt-1">{versionSource.version === 'current' ? '当前版（wf）' : '草稿版（saved）'}</dd></div>
          <div><dt className="text-muted-foreground">Pack</dt><dd className="mt-1 break-all">{versionSource.packId}</dd></div>
          <div><dt className="text-muted-foreground">分支 / Tag</dt><dd className="mt-1 break-all">{versionSource.baseRef}</dd></div>
          <div><dt className="text-muted-foreground">Commit</dt><dd className="mt-1 break-all">{versionSource.baseCommit}</dd></div>
        </dl>
        <h4 className="mt-5 text-xs font-medium text-muted-foreground">保存状态</h4>
        <p className="mt-2 text-xs">{saveStateText}</p>
        <p className="mt-3 text-xs text-muted-foreground">保存与部署由上层统一处理；刷新或关闭页面会丢失未保存更改。</p>
      </aside>
    </div>
  </section>
}
