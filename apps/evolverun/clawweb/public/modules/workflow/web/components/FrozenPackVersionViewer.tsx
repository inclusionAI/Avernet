import { useState } from 'react'
import type { PackVersionFile } from './PackVersionEditor'

/** Trigger a user-requested local download; never interpolate paths into HTML or URLs. */
export function downloadLocalBlob(blob: Blob, filename: string): boolean {
  let url: string | undefined
  let link: HTMLAnchorElement | undefined
  try {
    url = URL.createObjectURL(blob)
    link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    const created = url
    const revoke = URL.revokeObjectURL.bind(URL)
    setTimeout(() => revoke(created), 1000)
    return true
  } catch {
    if (url) URL.revokeObjectURL(url)
    return false
  } finally { link?.remove() }
}

function decodeBytes(base64: string): Uint8Array {
  return Uint8Array.from(atob(base64), char => char.charCodeAt(0))
}
function displayText(base64: string): string | null {
  try {
    const bytes = decodeBytes(base64)
    if (bytes.byteLength > 1024 * 1024) return null
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
    // Textareas normalize CR and control characters, so do not claim byte fidelity.
    return /[\x00-\x08\x0b-\x1f\x7f-\x9f]/u.test(text) ? null : text
  } catch { return null }
}

/** Read-only representation; binary downloads preserve bytes and modes are displayed. */
export default function FrozenPackVersionViewer({ files }: { files: PackVersionFile[] }) {
  const [selectedPath, setSelectedPath] = useState<string | null>(() => files[0]?.path ?? null)
  const [error, setError] = useState(false)
  const selected = files.find(file => file.path === selectedPath)
  const text = selected ? displayText(selected.contentBase64) : null
  return <section aria-label="冻结版本文件" className="min-w-0 space-y-2">
    <p className="text-[11px] leading-relaxed text-muted-foreground">对账期间只读；文本可复制，二进制可下载。文件内容可能包含敏感信息。</p>
    <div className="flex min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card md:flex-row">
      <aside aria-label="冻结 Pack 文件导航" className="min-w-0 shrink-0 border-b border-border bg-muted/30 p-3 md:w-[235px] md:border-b-0 md:border-r">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-[13px] font-semibold text-foreground">Pack 文件</h3>
          <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{files.length} 个文件</span>
        </div>
        <div className="max-h-48 overflow-y-auto p-1 md:max-h-[min(60vh,36rem)]">
          {files.length === 0 ? <p className="py-3 text-[11px] text-muted-foreground">没有文件。</p> : <ul aria-label="冻结文件列表" className="space-y-1.5">{files.map(file => <li key={file.path}>
            <button type="button" aria-label={file.path} title={file.path} aria-pressed={selected === file} onClick={() => { setSelectedPath(file.path); setError(false) }} className="group flex min-h-[35px] w-full cursor-pointer items-center gap-2 rounded-[7px] border border-input/60 bg-background px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 aria-pressed:border-primary/50 aria-pressed:bg-primary/10 aria-pressed:text-primary disabled:cursor-not-allowed disabled:opacity-40">
              <svg aria-hidden="true" className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 16 16"><path d="M9 2.5H4A1.5 1.5 0 0 0 2.5 4v8A1.5 1.5 0 0 0 4 13.5h8a1.5 1.5 0 0 0 1.5-1.5V7L9 2.5Z" /><path d="M9 2.5V7h4.5" /></svg>
              <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
              <span aria-hidden="true" className="shrink-0 opacity-0 group-aria-pressed:opacity-100">✓</span>
            </button>
          </li>)}</ul>}
        </div>
      </aside>
      {selected ? <section aria-label="冻结所选文件" className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="break-all font-mono text-[12px] font-semibold text-foreground">{selected.path.split('/').pop()}</h3>
            <p className="mt-1 break-all font-mono text-[10px] leading-relaxed text-muted-foreground">路径：{selected.path} · 模式：{selected.mode}</p>
          </div>
          <span className="shrink-0 rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">只读</span>
        </header>
        {text === null
          ? <div className="flex min-h-48 flex-1 items-center justify-center bg-background p-6">
            <p className="max-w-md rounded-lg border border-border bg-muted/30 p-4 text-[11px] leading-relaxed text-muted-foreground">二进制、无法安全显示的文本或大文件；请下载原始字节。</p>
          </div>
          : <textarea aria-label="文件内容" readOnly spellCheck={false} wrap="off" className="block h-[clamp(16rem,50vh,32rem)] w-full min-w-0 resize-none overflow-auto border-0 bg-background px-4 py-4 font-mono text-[12px] leading-[1.8] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/30" value={text} />}
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2.5">
          <span className="text-[10px] text-muted-foreground">{text === null ? '原始字节保持不变' : 'UTF-8 · 可选中并复制文本'}</span>
          <button type="button" className="inline-flex min-h-[31px] max-w-full cursor-pointer items-center justify-center rounded-[7px] border border-input bg-card px-3 py-1.5 text-[11px] font-semibold text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => {
            try { setError(!downloadLocalBlob(new Blob([decodeBytes(selected.contentBase64) as BlobPart], { type: 'application/octet-stream' }), 'pack-version-file.bin')) }
            catch { setError(true) }
          }}>下载所选文件原始字节</button>
        </footer>
        {error && <p role="alert" className="mx-4 mb-3 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-[11px] leading-relaxed text-destructive">下载失败；候选仍保留在当前页面会话中。</p>}
      </section> : <p className="p-4 text-[11px] text-muted-foreground">没有可预览的文件。</p>}
    </div>
  </section>
}
