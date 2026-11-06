import { createDeploymentId } from './deploymentGuard'

/** Editor database and legacy Git writes are guarded per browser tab, independently of QueryClient.
 * sessionStorage survives a same-tab reload; another tab is not coordinated. */
type Phase = 'clear' | 'pending' | 'unknown'
type Entry = { phase: Phase; epoch: number; owner?: string }
type Guard = { entries: Map<string, Entry>; listeners: Set<() => void> }
const key = Symbol.for('avernet.editor.workflow-write-guard.v1')
const root = globalThis as typeof globalThis & { [key: symbol]: unknown }
const guard = (root[key] ??= { entries: new Map(), listeners: new Set() }) as Guard
const prefix = 'avernet.editor.pending-workflow-write.v1:'
const storageKey = (id: string) => prefix + encodeURIComponent(id)
const notify = () => guard.listeners.forEach(listener => listener())
const manual = '数据库写入结果未终结，需人工核对；此标记也可能表示 Git 文件写入结果未知，请同时核对数据库与 Git。不要重试或将读取结果作为已确认基线。'

function entry(id: string): Entry {
  let current = guard.entries.get(id)
  if (current?.phase !== 'clear' && current) return current
  try {
    const persisted = sessionStorage.getItem(storageKey(id))
    if (persisted !== null) {
      // A marker surviving reload is never proof of an unfinished or finished
      // request: both possibilities require reconciliation.
      current = { phase: 'unknown', epoch: (current?.epoch ?? 0) + 1 }
      guard.entries.set(id, current)
      return current
    }
    if (!current) { current = { phase: 'clear', epoch: 0 }; guard.entries.set(id, current) }
    return current
  } catch {
    current = { phase: 'unknown', epoch: (current?.epoch ?? 0) + 1 }
    guard.entries.set(id, current)
    return current
  }
}
export function editorWorkflowWriteState(id: string): Readonly<Entry> {
  return id ? entry(id) : { phase: 'clear', epoch: 0 }
}
export function editorWorkflowWriteSnapshot(id: string): string {
  const value = editorWorkflowWriteState(id)
  return `${value.phase}:${value.epoch}`
}
export function subscribeEditorWorkflowWrites(listener: () => void): () => void {
  guard.listeners.add(listener)
  return () => { guard.listeners.delete(listener) }
}
export function assertEditorWorkflowReadable(id: string): number {
  const value = entry(id)
  if (value.phase !== 'clear') throw new Error(value.phase === 'pending' ? '数据库写入正在进行；请等待结果，原草稿仍保留。此状态也可能表示 Git 文件写入进行中。' : manual)
  return value.epoch
}
export function editorWorkflowReadIsCurrent(id: string, epoch: number): boolean {
  const value = entry(id)
  return value.phase === 'clear' && value.epoch === epoch
}

/** The marker is installed and read back before network dispatch. */
export function beginEditorWorkflowWrite(id: string): string {
  const previous = entry(id)
  if (previous.phase !== 'clear') throw new Error(previous.phase === 'pending' ? '数据库或 Git 文件写入正在进行；未发送保存。' : manual)
  let owner: string
  try {
    owner = createDeploymentId()
    const marker = JSON.stringify({ version: 1, owner })
    sessionStorage.setItem(storageKey(id), marker)
    if (sessionStorage.getItem(storageKey(id)) !== marker) throw new Error('marker verification failed')
  } catch {
    guard.entries.set(id, { phase: 'unknown', epoch: previous.epoch + 1 })
    notify()
    throw new Error('无法安全记录数据库写入状态；本标记也用于 Git 文件写入。未发送保存。请人工核对并检查浏览器存储。')
  }
  guard.entries.set(id, { phase: 'pending', epoch: previous.epoch + 1, owner })
  notify()
  return owner
}

/** Only the owner of a terminal response can clear its marker. */
export function finishEditorWorkflowWrite(id: string, owner: string): void {
  const current = guard.entries.get(id)
  try {
    const expected = JSON.stringify({ version: 1, owner })
    if (!current || current.phase !== 'pending' || current.owner !== owner || sessionStorage.getItem(storageKey(id)) !== expected) throw new Error('marker ownership mismatch')
    sessionStorage.removeItem(storageKey(id))
    if (sessionStorage.getItem(storageKey(id)) !== null) throw new Error('marker removal failed')
    guard.entries.set(id, { phase: 'clear', epoch: current.epoch + 1 })
    notify()
  } catch {
    guard.entries.set(id, { phase: 'unknown', epoch: (current?.epoch ?? 0) + 1 })
    notify()
    throw new Error('无法确认或清除数据库或 Git 写入标记；请人工核对，勿重试。')
  }
}
export function abandonEditorWorkflowWrite(id: string, owner: string): void {
  const current = guard.entries.get(id)
  if (current?.phase === 'pending' && current.owner === owner) {
    guard.entries.set(id, { phase: 'unknown', epoch: current.epoch + 1 })
    notify()
  }
}
