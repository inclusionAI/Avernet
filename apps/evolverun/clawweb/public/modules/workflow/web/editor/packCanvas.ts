import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { PackVersionFile } from '../components/PackVersionEditor'
import { parseWorkflowYaml, normalizationChanges } from './yamlDraft'
import { parseLosslessWorkflowText } from './losslessNumbers'
import type { Snapshot } from './packVersionSession'

function decode(file: PackVersionFile): string {
  return new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(file.contentBase64), c => c.charCodeAt(0)))
}
function encode(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

/** Layout is a sidecar. DB legacy coordinates must not masquerade as workflow changes. */
export function executionSpec(spec: WorkflowSpec): WorkflowSpec {
  return { ...spec, nodes: spec.nodes.map(node => {
    const { _x, _y, ...execution } = node
    return execution
  }) }
}
export function sameExecutionSpec(a: WorkflowSpec, b: WorkflowSpec): boolean {
  return normalizationChanges(executionSpec(a), executionSpec(b)).length === 0
}
/** The complete inventory uses Pack-relative paths for both root and nested layouts. */
export function packManifest(files: PackVersionFile[], id: string): { packId: string; path: string } {
  const manifests = files.filter(file => file.path === 'workflow.pack.yaml')
  if (manifests.length !== 1) throw new Error('Pack 缺少 workflow.pack.yaml，无法确定画布 YAML 路径。')
  const manifest = parse(decode(manifests[0]), { uniqueKeys: true, maxAliasCount: 100 })
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)
    || typeof manifest.id !== 'string' || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(manifest.id)
    || /[^A-Za-z0-9_.-]/.test(manifest.id) || manifest.id.includes('..') || manifest.id.endsWith('.') || manifest.id.endsWith('.lock')) throw new Error('Pack manifest ID 无效。')
  if (!Array.isArray(manifest.workflows)) throw new Error('Pack manifest 必须声明工作流。')
  const entries = manifest.workflows.filter((entry: unknown) => typeof entry === 'string' ? entry === id
    : entry && typeof entry === 'object' && !Array.isArray(entry) && (entry as { id?: unknown }).id === id)
  if (entries.length !== 1) throw new Error('Pack manifest 必须唯一声明当前工作流。')
  const path = typeof entries[0] === 'string' ? `workflows/${id}.yaml` : entries[0].file
  if (typeof path !== 'string' || /^[A-Za-z]:/.test(path) || /[\\\x00-\x1f]/.test(path)
    || path.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')) throw new Error('manifest workflow path 无效。')
  return { packId: manifest.id, path }
}

export function packWorkflow(files: PackVersionFile[], id: string, expectedPackId?: string): { packId: string; path: string; spec: WorkflowSpec } {
  const { packId, path } = packManifest(files, id)
  // Read snapshots must match their declared identity. Candidates may intentionally rename the Pack.
  if (expectedPackId !== undefined && packId !== expectedPackId) throw new Error('Pack manifest 身份与 Git 基线不一致。')
  const file = files.find(file => file.path === path)
  if (!file) throw new Error('manifest 指向的工作流 YAML 不在完整版本中。')
  const spec = parseWorkflowYaml(decode(file))
  if (spec.id !== id) throw new Error('工作流 YAML 身份与当前画布不一致。')
  return { packId, path, spec }
}

/** Read an old Git baseline without treating missing legacy fields as a candidate spec. */
export function packWorkflowBaseline(files: PackVersionFile[], id: string, expectedPackId?: string): { packId: string; path: string; spec: WorkflowSpec | null } {
  const { packId, path } = packManifest(files, id)
  if (expectedPackId !== undefined && packId !== expectedPackId) throw new Error('Pack manifest 身份与 Git 基线不一致。')
  const file = files.find(file => file.path === path)
  if (!file) throw new Error('manifest 指向的工作流 YAML 不在完整版本中。')
  const text = decode(file)
  const value: unknown = parseLosslessWorkflowText(text)
  if (!value || typeof value !== 'object' || Array.isArray(value) || (value as { id?: unknown }).id !== id) {
    throw new Error('工作流 YAML 身份与当前画布不一致。')
  }
  const record = value as Record<string, unknown>
  if (Object.hasOwn(record, 'title') && Object.hasOwn(record, 'nodes')) return { packId, path, spec: parseWorkflowYaml(text) }
  // Validate every present field under the same structural rules; do not expose
  // synthetic defaults as a real baseline or permit them in edited candidates.
  const supplemented = { ...record, ...(!Object.hasOwn(record, 'title') ? { title: 'legacy' } : {}),
    ...(!Object.hasOwn(record, 'nodes') ? { nodes: [] } : {}) }
  let json: string
  try { json = JSON.stringify(supplemented) } catch { throw new Error('Git 基线包含循环引用，无法安全读取。') }
  parseWorkflowYaml(json)
  return { packId, path, spec: null }
}

/** Merge only the workflow file; unrelated resources retain their exact bytes and modes. */
export function mergeCanvasFiles(snapshot: Snapshot, files: PackVersionFile[], baseline: WorkflowSpec, canvas: WorkflowSpec): PackVersionFile[] {
  const original = packWorkflow(snapshot.versionFiles, snapshot.workflowId, snapshot.packId)
  if (!sameExecutionSpec(original.spec, baseline)) throw new Error('保存冲突：数据库初始文档与 Git 基线不一致，请明确选择 Git 版本后再编辑。')
  const candidate = packWorkflow(files, snapshot.workflowId)
  if (sameExecutionSpec(canvas, baseline)) return files
  if (!sameExecutionSpec(candidate.spec, original.spec) && !sameExecutionSpec(candidate.spec, canvas)) {
    throw new Error('保存冲突：画布和文件编辑同时修改了工作流 YAML，请先统一两处内容；其他 Pack 修改仍保留。')
  }
  if (sameExecutionSpec(candidate.spec, canvas)) return files
  const spec = executionSpec(canvas)
  // Preserve any coordinates already present in Git, without importing DB-only layout.
  spec.nodes = spec.nodes.map(node => {
    const old = candidate.spec.nodes.find(item => item.id === node.id)
    return { ...node, ...(old && Object.hasOwn(old, '_x') ? { _x: old._x } : {}), ...(old && Object.hasOwn(old, '_y') ? { _y: old._y } : {}) }
  })
  const contentBase64 = encode(stringify(spec, { lineWidth: 0 }))
  return files.map(file => file.path === candidate.path ? { ...file, contentBase64 } : file)
}
