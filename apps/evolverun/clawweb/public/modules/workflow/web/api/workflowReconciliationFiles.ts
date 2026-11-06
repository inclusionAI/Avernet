import type { Change } from '../editor/packVersionSession'
import type { PackVersionFile } from '../components/PackVersionEditor'

export type ReconciliationFile = PackVersionFile
export type DisplayFile = { kind: 'text'; text: string } | { kind: 'binary' }

/** Fail closed on invisible/default-ignorable Unicode and non-renderable code
 * points, including supplementary characters. Preserve ordinary text layout. */
export function containsUnsafeReviewText(text: string): boolean {
  return /[\p{C}\p{Default_Ignorable_Code_Point}\p{Zl}\p{Zp}]/u.test(text.replace(/[\t\n\r]/g, ''))
}

/** Never decode with replacement or present control characters as reviewed text. */
export function displayReconciliationFile(file: ReconciliationFile): DisplayFile {
  try {
    const bytes = Uint8Array.from(atob(file.contentBase64), c => c.charCodeAt(0))
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes)
    if (containsUnsafeReviewText(text)) return { kind: 'binary' }
    return { kind: 'text', text }
  } catch { return { kind: 'binary' } }
}

function fileMap(files: ReconciliationFile[]): Map<string, ReconciliationFile> {
  if (!Array.isArray(files)) throw new Error('缺少完整文件快照；未确认。')
  const map = new Map<string, ReconciliationFile>()
  for (const f of files) {
    if (!f || typeof f.path !== 'string' || !f.path || f.path.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')
      || /[\\\x00-\x1f]/u.test(f.path) || containsUnsafeReviewText(f.path) || /^[A-Za-z]:/.test(f.path)
      || typeof f.contentBase64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(f.contentBase64)
      || (f.mode !== '100644' && f.mode !== '100755') || map.has(f.path)
      || Object.keys(f).some(key => !['path', 'contentBase64', 'mode'].includes(key))) throw new Error('文件快照无效；未确认。')
    try { if (btoa(atob(f.contentBase64)) !== f.contentBase64) throw new Error() } catch { throw new Error('文件内容无效；未确认。') }
    map.set(f.path, f)
  }
  for (const path of map.keys()) {
    const parts = path.split('/')
    for (let i = 1; i < parts.length; i++) if (map.has(parts.slice(0, i).join('/'))) throw new Error('文件路径冲突；未确认。')
  }
  return map
}

/** Check the exact before/after snapshots explain every non-workflow request change. */
export function validateReconciliationFiles(before: ReconciliationFile[], after: ReconciliationFile[], changes: Change[], workflowPath: string, gitYaml: string): void {
  const old = fileMap(before), next = fileMap(after)
  const original = old.get(workflowPath)
  if (!original || !next.get(workflowPath) || next.get(workflowPath)!.mode !== original.mode) throw new Error('缺少工作流文件或文件模式已变化；未确认。')
  try {
    const bytes = Uint8Array.from(atob(original.contentBase64), c => c.charCodeAt(0))
    if (new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes) !== gitYaml) throw new Error()
  } catch { throw new Error('Git YAML 原文与文件快照不一致；未确认。') }
  const expected = new Map<string, Change>()
  for (const path of new Set([...old.keys(), ...next.keys()])) {
    if (path === workflowPath) continue
    const a = old.get(path), b = next.get(path)
    if (!b) expected.set(path, { path, operation: 'delete' })
    else if (!a || a.mode !== b.mode || a.contentBase64 !== b.contentBase64) expected.set(path, { path, operation: a ? 'replace' : 'create', contentBase64: b.contentBase64, mode: b.mode })
  }
  if (changes.length !== expected.size) throw new Error('文件快照与提交变更不一致；未确认。')
  const seen = new Set<string>()
  for (const change of changes) {
    const e = change && expected.get(change.path)
    if (!e || seen.has(change.path) || e.operation !== change.operation
      || (e.operation !== 'delete' && (change.operation === 'delete' || change.contentBase64 !== e.contentBase64 || change.mode !== e.mode))
      || Object.keys(change).some(key => !['path', 'operation', ...(e.operation === 'delete' ? [] : ['mode', 'contentBase64'])].includes(key))) throw new Error('文件快照与提交变更不一致；未确认。')
    seen.add(change.path)
  }
}

export function reconciliationHasBinaryFiles(before: ReconciliationFile[], after: ReconciliationFile[]): boolean {
  return [...before, ...after].some(file => displayReconciliationFile(file).kind === 'binary')
}
