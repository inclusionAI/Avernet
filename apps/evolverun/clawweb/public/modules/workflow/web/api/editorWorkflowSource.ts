import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { isMap, isScalar, isSeq, parseDocument, visit } from 'yaml'
import { normalizationChanges, parseWorkflowYaml } from '../editor/yamlDraft'
import { parseLosslessWorkflowText } from '../editor/losslessNumbers'
import { editorDraftDocument, readEditorDraftCas } from './editorDraftCas'
import { assertEditorWorkflowReadable, editorWorkflowReadIsCurrent } from '../editor/workflowWriteGuard'
import { validateSnapshot } from '../editor/packVersionSession'
import { packManifest } from '../editor/packCanvas'

export type EditorWorkflowSource = { spec: WorkflowSpec; databaseSpec: WorkflowSpec | null; activeDeployNumber: number | null }

/** Host-supplied, read-only authority: it must independently authenticate and
 * authorize the immutable release for this exact active DB row. A row hash,
 * client assertion, or unverified Git tag is not such an authority. The text
 * returned is the entire authored workflow, not an inferred content envelope.
 * No default authority is installed. */
export type TrustedActiveHistoryIdentity = Readonly<{
  workflowId: string; packId: string; deployNumber: number; version: number;
  activeToken: string; tagName: string | null; action: string;
}>
export type TrustedActiveHistoryReader = (identity: TrustedActiveHistoryIdentity) => Promise<
  Readonly<TrustedActiveHistoryIdentity & { workflowText: string }> | null
>
export type EditorWorkflowReadOptions = Readonly<{ readTrustedActiveHistory?: TrustedActiveHistoryReader }>

// Metadata stays out of the authored document and is tied to this exact object,
// not to a workflow ID or a later cache/refetch. Undefined means no evidence.
// Evidence is attached to the actual read, never reconstructed from an ID.
const readEpochs = new WeakMap<WorkflowSpec, { id: string; epoch: number; document: WorkflowSpec }>()
export function editorWorkflowSourceIsCurrent(spec: WorkflowSpec, id: string): boolean {
  const evidence = readEpochs.get(spec)
  // Legacy, locally constructed documents are only admissible before any
  // guarded write for this workflow. After a write they have no read proof.
  return evidence ? evidence.id === id && !normalizationChanges(evidence.document, spec).length && editorWorkflowReadIsCurrent(id, evidence.epoch) : editorWorkflowReadIsCurrent(id, 0)
}
const draftBindings = new WeakMap<WorkflowSpec, { packId: string | null; document: WorkflowSpec }>()
export function originalDraftPackId(spec: WorkflowSpec | null): string | null | undefined {
  if (!spec) return undefined
  const evidence = draftBindings.get(spec)
  return evidence && !normalizationChanges(evidence.document, spec).length ? evidence.packId : undefined
}

/** Evidence belongs to the displayed active snapshot, not a workflow-ID cache. */
export type ActiveDraftBaseline = { workflowId: string; packId: string; deployNumber: number; activeToken: string; active: Record<string, unknown>; specJson: string }
const activeBindings = new WeakMap<WorkflowSpec, { baseline: ActiveDraftBaseline; document: WorkflowSpec }>()
export function originalActiveDraftBaseline(spec: WorkflowSpec): ActiveDraftBaseline | undefined {
  const evidence = activeBindings.get(spec)
  return evidence && !normalizationChanges(evidence.document, spec).length ? structuredClone(evidence.baseline) : undefined
}
/** A validated session preparation may clone a source, but never fabricate evidence. */
export function carryActiveDraftBaseline(source: WorkflowSpec, prepared: WorkflowSpec): void {
  const read = readEpochs.get(source)
  if (read && !normalizationChanges(source, prepared).length) readEpochs.set(prepared, read)
  const baseline = originalActiveDraftBaseline(source)
  if (baseline && !normalizationChanges(source, prepared).length) activeBindings.set(prepared, { baseline, document: structuredClone(prepared) })
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** Only the exact GET contract means absence. An export/read/permission failure
 * must never authorize replacing a draft with a deployment snapshot. */
function missingDraft(cause: unknown, id: string): boolean {
  if (!record(cause) || cause.status !== 404 || typeof cause.body !== 'string') return false
  let body: unknown
  try { body = JSON.parse(cause.body) } catch { return false }
  if (!record(body) || (body.workflowId !== undefined && body.workflowId !== id)) return false
  if (body.code !== undefined) return body.code === 'WORKFLOW_NOT_FOUND'
  return body.error === 'Not Found' && body.message === `Workflow "${id}" not found`
}

/** null is a confirmed absent workflow_specs draft, not an unavailable service. */
export async function readDatabaseWorkflowDraft(id: string): Promise<WorkflowSpec | null> {
  let response: unknown
  try { response = await api.workflows.get(id) }
  catch (cause) { if (missingDraft(cause, id)) return null; throw cause }
  if (!record(response) || response.id !== id) throw new Error('工作流响应身份不匹配')
  const row = await readEditorDraftCas(id)
  const document = checkedDocument(editorDraftDocument(row.specJson, id), id)
  draftBindings.set(document, { packId: row.packId, document: structuredClone(document) })
  return document
}

function checkedDocument(value: unknown, id: string): WorkflowSpec {
  const spec = parseWorkflowYaml(JSON.stringify(value))
  if (spec.id !== id) throw new Error('工作流内容身份不匹配；未替换草稿')
  const version: unknown = spec.version
  // Legacy null is source data; normalization must be reviewed, not done on read.
  if (!(version === undefined || version === null || typeof version === 'string' && version.trim()
    || typeof version === 'number' && Number.isFinite(version))) throw new Error('工作流源版本结构无效；未替换草稿')
  return spec
}

/** Match the legacy writer's JSON-compatible tag whitelist. Parsing and
 * numeric checks remain lossless; non-JSON YAML tags must never be discarded. */
function parseHistoryText(text: string): unknown {
  const doc = parseDocument(text, { uniqueKeys: true })
  if (doc.errors.length || doc.warnings.length) throw new Error('数据库生效快照格式无效；未替换草稿')
  visit(doc, (_key, node) => {
    if (!node || typeof node !== 'object' || !('tag' in node) || !node.tag) return
    const tag = node.tag
    const supported = isScalar(node) && (
      tag === 'tag:yaml.org,2002:str' && typeof node.value === 'string'
      || (tag === 'tag:yaml.org,2002:int' || tag === 'tag:yaml.org,2002:float') && typeof node.value === 'number'
      || tag === 'tag:yaml.org,2002:bool' && typeof node.value === 'boolean'
      || tag === 'tag:yaml.org,2002:null' && node.value === null
    ) || tag === 'tag:yaml.org,2002:map' && isMap(node)
      || tag === 'tag:yaml.org,2002:seq' && isSeq(node)
    if (!supported) throw new Error('数据库生效快照含有无法无损转换的 YAML 标签；未替换草稿')
  })
  return parseLosslessWorkflowText(text)
}

class HistoryAmbiguityError extends Error {}
class HistoryContentResolution extends Error {
  constructor(readonly outer: Record<string, unknown>) { super('生效快照的 content 需要 Git 核验；未替换草稿。') }
}

/** Decode historical snapshots without modifying the stored row. */
function snapshotDocument(text: string, id: string): WorkflowSpec {
  const outer: unknown = parseHistoryText(text)
  // A whole-document string has no sibling fields to discard. Decode exactly
  // one layer, with the same lossless and ambiguity checks as an object row.
  // A string stored in an object field must satisfy the full archive contract.
  const value: unknown = typeof outer === 'string' ? parseHistoryText(outer) : outer
  if (!record(value)) throw new Error('数据库生效快照结构无效；未替换草稿')
  if (Object.hasOwn(value, 'id') && value.id !== id) throw new Error('数据库生效快照内容身份不匹配；未替换草稿')
  return historyDocumentByContract(value, id)
}

/** JSON-compatible structural equality, including every object key and array item. */
function sameHistoryValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((item, index) => sameHistoryValue(item, right[index]))
  }
  if (!record(left) || !record(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length
    && keys.every(key => Object.hasOwn(right, key) && sameHistoryValue(left[key], right[key]))
}

function historyDocumentByContract(value: Record<string, unknown>, id: string): WorkflowSpec {
  // A valid archive projection can be byte-for-byte identical to a direct
  // authored document. Resolve either interpretation only against active Git.
  // Do not parse content first: direct user text may be arbitrary YAML or malformed.
  if (typeof value.content === 'string') throw new HistoryContentResolution(value)
  // The legacy writer produced exactly these two fields. Matching that shape
  // cannot prove provenance, so do not remove it or trust its contentSource.
  // Other top-level values and all nested names are preserved as user data.
  const marker = value.__clawweb_snapshot_v1
  if (record(marker) && Object.keys(marker).length === 2
    && Object.hasOwn(marker, 'version') && marker.version === 1
    && Object.hasOwn(marker, 'contentSource') && (marker.contentSource === 'generated' || marker.contentSource === 'authored')) {
    throw new HistoryAmbiguityError('生效快照含有无法验证来源的元数据；未替换草稿，请先核对历史版本。')
  }
  return checkedDocument(value, id)
}

/** Only the server's no-draft active path may issue this row-token evidence.
 * Return the complete manifest-selected Git document, not a normalized view. */
async function readGitVerifiedHistory(id: string, packId: string, token: string): Promise<unknown> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const raw = await Promise.race([
      fetchJson<unknown>(`/api/workflows/${encodeURIComponent(id)}/pack-version?version=current`, { cache: 'no-store', signal: controller.signal }),
      new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('timeout')) }, 10_000) }),
    ])
    if (!record(raw) || !record(raw.activeHistory) || Object.keys(raw.activeHistory).length !== 2
      || raw.activeHistory.protocol !== 'active-history-v1' || raw.activeHistory.activeToken !== token) throw new Error('Active history evidence mismatch')
    const snapshot = validateSnapshot(raw, id, 'current')
    if (snapshot.packId !== packId) throw new Error('Pack mismatch')
    const manifest = packManifest(snapshot.versionFiles, id)
    if (manifest.packId !== packId) throw new Error('Pack mismatch')
    const file = snapshot.versionFiles.find(item => item.path === manifest.path)
    if (!file || file.contentBase64.length > 1_398_104) throw new Error('Invalid workflow file')
    const bytes = Uint8Array.from(atob(file.contentBase64), char => char.charCodeAt(0))
    if (bytes.length > 1024 * 1024) throw new Error('Invalid workflow file')
    return parseHistoryText(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch {
    // Do not expose file paths, contents, Git errors, or credentials.
    throw new Error('无法验证生效 Git 工作流与历史全文一致；未替换草稿。')
  } finally { clearTimeout(timer) }
}

/** A token binds bytes and row metadata; it is not evidence of content origin.
 * Distinguish a definite mismatch from legacy/unavailable verification. */
async function activeSnapshotMatch(id: string, active: Record<string, unknown>, snapshot: Record<string, unknown>): Promise<'match' | 'mismatch' | 'unavailable'> {
  // Compare metadata that both endpoints actually supplied, even for old
  // servers without a token. Absence alone is not a contradiction.
  const tagKnown = (value: unknown) => value === null || typeof value === 'string'
  if (tagKnown(active.tagName) && tagKnown(snapshot.tagName) && active.tagName !== snapshot.tagName) return 'mismatch'
  if (typeof active.action === 'string' && typeof snapshot.action === 'string' && active.action !== snapshot.action) return 'mismatch'
  if (typeof active.activeToken !== 'string' || !/^[a-f0-9]{64}$/.test(active.activeToken)) return 'unavailable'
  if (!tagKnown(active.tagName) || typeof active.action !== 'string') return 'unavailable'
  // A token is meaningful only for the exact full set of returned metadata.
  if (snapshot.tagName !== active.tagName || snapshot.action !== active.action) return 'mismatch'
  if (typeof active.packId !== 'string' || typeof snapshot.specJson !== 'string'
    || !Number.isSafeInteger(active.deployNumber) || !Number.isSafeInteger(active.version) || !globalThis.crypto?.subtle) return 'unavailable'
  const tuple = ['active-workflow-baseline-v1', id, active.packId, String(active.deployNumber), String(active.version),
    active.tagName, active.action, snapshot.specJson]
  try {
    const bytes = new TextEncoder().encode(JSON.stringify(tuple))
    const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('') === active.activeToken ? 'match' : 'mismatch'
  } catch { return 'unavailable' }
}

/** Reobserve the original row, not a newly decoded history document. This
 * does not establish provenance. These separate reads are only a preflight, never an
 * atomic snapshot or a replacement for the server's first-draft transaction. */
export async function verifyOriginalActiveDraftBaseline(id: string, baseline: ActiveDraftBaseline): Promise<void> {
  const epoch = assertEditorWorkflowReadable(id)
  if (baseline.workflowId !== id || !record(baseline.active)
    || baseline.active.isActive !== true || baseline.active.packId !== baseline.packId
    || baseline.active.deployNumber !== baseline.deployNumber || baseline.active.activeToken !== baseline.activeToken
    || !Number.isSafeInteger(baseline.deployNumber) || baseline.deployNumber <= 0
    || !Number.isSafeInteger(baseline.active.version) || typeof baseline.specJson !== 'string') {
    throw new Error('原始生效版本基线无效')
  }
  if (await readDatabaseWorkflowDraft(id) !== null) throw new Error('数据库草稿已存在')
  const history = await api.workflows.getHistory(id, 50, true)
  if (!record(history) || history.workflowId !== id || !Array.isArray(history.history)
    || !record(history.active) || normalizationChanges(baseline.active, history.active).length) {
    throw new Error('数据库生效版本已变化')
  }
  const snapshot = await api.workflows.getDeploySnapshot(id, baseline.deployNumber)
  if (!record(snapshot) || snapshot.workflowId !== id || snapshot.deployNumber !== baseline.deployNumber
    || snapshot.version !== baseline.active.version || snapshot.specJson !== baseline.specJson
    || await activeSnapshotMatch(id, history.active, snapshot) !== 'match') {
    throw new Error('数据库生效快照已变化')
  }
  // Catch a draft created while history/snapshot were being read. A competing
  // client can still write after this check; the server must atomically check
  // both draft absence and active token with insertion.
  if (await readDatabaseWorkflowDraft(id) !== null) throw new Error('数据库草稿已存在')
  if (!editorWorkflowReadIsCurrent(id, epoch)) throw new Error('读取期间数据库写入状态已变化')
}

/** Draft first. Only confirmed absence permits falling back to the active DB
 * deployment; the latest history entry (or a Git tree) is never this source. */
async function readEditorWorkflowSourceUnchecked(id: string, options?: EditorWorkflowReadOptions): Promise<EditorWorkflowSource> {
  const databaseSpec = await readDatabaseWorkflowDraft(id)
  if (databaseSpec !== null) return { spec: structuredClone(databaseSpec), databaseSpec, activeDeployNumber: null }
  const history = await api.workflows.getHistory(id, 50, true)
  if (!record(history) || history.workflowId !== id || !Array.isArray(history.history)
    || !Object.hasOwn(history, 'active')) throw new Error('数据库工作流历史身份或结构不匹配；未替换草稿')
  const active = history.active
  if (active === null) throw new Error('数据库没有暂存稿或生效版本；未替换草稿')
  if (!record(active) || active.isActive !== true || !Number.isSafeInteger(active.deployNumber)
    || active.deployNumber <= 0 || !Number.isSafeInteger(active.version)) throw new Error('数据库生效版本结构无效；未替换草稿')
  const snapshot = await api.workflows.getDeploySnapshot(id, active.deployNumber)
  if (!record(snapshot) || snapshot.workflowId !== id || snapshot.deployNumber !== active.deployNumber
    || snapshot.version !== active.version || typeof snapshot.specJson !== 'string') {
    throw new Error('数据库生效快照身份或版本不匹配；未替换草稿')
  }
  const match = await activeSnapshotMatch(id, active, snapshot)
  if (match === 'mismatch') throw new Error('数据库生效快照与生效版本校验不匹配；未替换草稿')
  let spec: WorkflowSpec
  try {
    spec = snapshotDocument(snapshot.specJson, id)
  } catch (cause) {
    if (cause instanceof HistoryContentResolution) {
      // A hash mismatch, unavailable crypto, or legacy server without a bound
      // active token cannot safely distinguish direct text from an archive.
      if (match !== 'match' || typeof active.packId !== 'string' || typeof active.activeToken !== 'string') {
        throw new Error('无法核验生效历史与 Git；未替换草稿。')
      }
      const outer = cause.outer
      const git = await readGitVerifiedHistory(id, active.packId, active.activeToken)
      if (sameHistoryValue(git, outer)) {
        spec = checkedDocument(outer, id)
      } else {
        const document = parseHistoryText(outer.content as string)
        if (!record(document) || document.id !== id) throw new Error('数据库生效快照内容身份不匹配；未替换草稿')
        if (!sameHistoryValue(outer, { ...document, content: outer.content }) || !sameHistoryValue(git, document)) {
          throw new Error('数据库生效快照与 Git 工作流不一致；未替换草稿。')
        }
        spec = checkedDocument(document, id)
      }
    } else {
      // Existing optional authority remains limited to non-content ambiguity.
      if (!(cause instanceof HistoryAmbiguityError) || !options?.readTrustedActiveHistory || match !== 'match'
        || typeof active.packId !== 'string' || typeof active.activeToken !== 'string'
        || !(active.tagName === null || typeof active.tagName === 'string') || typeof active.action !== 'string') throw cause
      const identity: TrustedActiveHistoryIdentity = Object.freeze({ workflowId: id, packId: active.packId,
        deployNumber: active.deployNumber, version: active.version, activeToken: active.activeToken,
        tagName: active.tagName, action: active.action })
      const resolved = await options.readTrustedActiveHistory(identity)
      if (!resolved || (Object.keys(identity) as Array<keyof TrustedActiveHistoryIdentity>).some(key => resolved[key] !== identity[key])
        || typeof resolved.workflowText !== 'string') throw cause
      spec = checkedDocument(parseHistoryText(resolved.workflowText), id)
    }
  }
  // These reads detect changes during recovery but are not an atomic snapshot.
  // The server must still recheck draft absence and active baseline atomically
  // with any eventual first-draft write.
  if (await readDatabaseWorkflowDraft(id) !== null) throw new Error('读取期间数据库草稿已变化；未替换草稿')
  const current = await api.workflows.getHistory(id, 50, true)
  if (!record(current) || current.workflowId !== id || !Array.isArray(current.history)
    || !record(current.active) || normalizationChanges(active, current.active).length) {
    throw new Error('读取期间数据库生效版本已变化；未替换草稿')
  }
  const currentSnapshot = await api.workflows.getDeploySnapshot(id, active.deployNumber)
  if (!record(currentSnapshot) || currentSnapshot.workflowId !== id
    || currentSnapshot.deployNumber !== snapshot.deployNumber || currentSnapshot.version !== snapshot.version
    || currentSnapshot.tagName !== snapshot.tagName || currentSnapshot.action !== snapshot.action
    || currentSnapshot.specJson !== snapshot.specJson) {
    throw new Error('读取期间数据库生效快照已变化；未替换草稿')
  }
  const currentMatch = await activeSnapshotMatch(id, current.active, currentSnapshot)
  if (currentMatch === 'mismatch' || match === 'match' && currentMatch !== 'match') {
    throw new Error('读取期间数据库生效版本已变化；未替换草稿')
  }
  // Catch a draft created while the history and snapshot were re-read.
  if (await readDatabaseWorkflowDraft(id) !== null) throw new Error('读取期间数据库草稿已变化；未替换草稿')

  // Older servers and unavailable crypto remain readable, without inventing
  // evidence required for a first draft save.
  if (match === 'match' && typeof active.packId === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(active.packId)
    && !active.packId.includes('..') && !active.packId.endsWith('.') && !active.packId.endsWith('.lock')
    && typeof active.activeToken === 'string') {
    activeBindings.set(spec, { baseline: { workflowId: id, packId: active.packId, deployNumber: active.deployNumber, activeToken: active.activeToken,
      active: structuredClone(active), specJson: snapshot.specJson }, document: structuredClone(spec) })
  }
  return { spec, databaseSpec: null, activeDeployNumber: active.deployNumber }
}

/** Navigation reads are invalidated if any write starts while they are in flight. */
export async function readEditorWorkflowSource(id: string, options?: EditorWorkflowReadOptions): Promise<EditorWorkflowSource> {
  const epoch = assertEditorWorkflowReadable(id)
  const source = await readEditorWorkflowSourceUnchecked(id, options)
  if (!editorWorkflowReadIsCurrent(id, epoch)) throw new Error('读取期间数据库写入状态已变化；未替换草稿。')
  readEpochs.set(source.spec, { id, epoch, document: structuredClone(source.spec) })
  return source
}
