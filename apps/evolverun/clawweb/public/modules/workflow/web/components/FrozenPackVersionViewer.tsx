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
  return <section aria-label="冻结版本文件" className="space-y-3">
    <p>对账期间只读；文本可复制，二进制可下载。文件内容可能包含敏感信息。</p>
    {files.length === 0 ? <p>没有文件。</p> : <ul aria-label="冻结文件列表">{files.map(file => <li key={file.path}>
      <button type="button" aria-pressed={selected === file} onClick={() => { setSelectedPath(file.path); setError(false) }}>{file.path}</button>
    </li>)}</ul>}
    {selected && <section aria-label="冻结所选文件" className="space-y-2">
      <p className="break-all">路径：{selected.path} · 模式：{selected.mode}</p>
      {text === null ? <p>二进制、无法安全显示的文本或大文件；请下载原始字节。</p>
        : <label className="grid gap-1">文件内容<textarea readOnly className="min-h-48 w-full rounded border border-input bg-background p-2 font-mono" value={text} /></label>}
      <button type="button" onClick={() => {
        try { setError(!downloadLocalBlob(new Blob([decodeBytes(selected.contentBase64) as BlobPart], { type: 'application/octet-stream' }), 'pack-version-file.bin')) }
        catch { setError(true) }
      }}>下载所选文件原始字节</button>
      {error && <p role="alert">下载失败；候选仍保留在当前页面会话中。</p>}
    </section>}
  </section>
}
