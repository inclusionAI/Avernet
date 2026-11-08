import { create } from 'zustand'
import type { WorkflowSpec, WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { getResultBranchIds, getResultBranches, withResultBranches } from './contracts'
import { branchEditError, canConnect, computeDagLayout, dependencies, findNodeReferences, graphMutationError, validateSpec, type Position, type ValidationError } from './graph'

export { validateSpec } from './graph'
export type { ValidationError } from './graph'

export const EXECUTOR_TYPES = [
  'embedded-agent', 'action', 'human', 'loop-group', 'collaboration', 'done',
  'subagent', 'bcs-route', 'baas-call', 'mcp-call', 'cli-script', 'subworkflow', 'approval',
] as const
export type ExecutorType = (typeof EXECUTOR_TYPES)[number]
export interface EditorNode extends WorkflowNode { _x?: number; _y?: number }

type Positions = Record<string, Position>
interface Snapshot { spec: WorkflowSpec | null; positions: Positions; selectedNodeId: string | null }
export interface EditorState extends Snapshot {
  /** Monotonic editor lifetime, independent of workflow ID, saved baseline and undo history. */
  sessionId: number
  isDirty: boolean
  validationErrors: ValidationError[]
  pendingEdits: Record<string, string>
  actionError: string | null
  canUndo: boolean
  canRedo: boolean
  loadSpec: (spec: WorkflowSpec) => void
  createNew: (id: string, title: string) => void
  updateSpecField: (field: string, value: unknown) => void
  addNode: (node: EditorNode) => void
  updateNode: (nodeId: string, updates: Partial<WorkflowNode>) => void
  removeNode: (nodeId: string) => void
  duplicateNode: (nodeId: string) => void
  moveNode: (nodeId: string, x: number, y: number) => void
  addEdge: (sourceId: string, targetId: string, branchId?: string) => void
  removeEdge: (sourceId: string, targetId: string) => void
  selectNode: (nodeId: string | null) => void
  setValidationErrors: (errors: ValidationError[]) => void
  setPendingEdit: (key: string, message: string | null) => void
  setActionError: (message: string | null) => void
  beginTransaction: () => void
  endTransaction: () => void
  undo: () => void
  redo: () => void
  completeSave: (savedSpec: WorkflowSpec, specAtRequest: WorkflowSpec, sessionId: number) => boolean
  markClean: (savedSpec?: WorkflowSpec) => void
  importYaml: (spec: WorkflowSpec) => void
  reset: (options?: { force?: boolean }) => void
}
interface HistoryState {
  _past: Snapshot[]
  _future: Snapshot[]
  _savedSpec: WorkflowSpec | null
  _transaction: Snapshot | null
  _transactionDepth: number
}
type StoreState = EditorState & HistoryState
const HISTORY_LIMIT = 50
const clone = <T,>(value: T): T => structuredClone(value)

function equal(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ak = Object.keys(a), bk = Object.keys(b)
  return ak.length === bk.length && ak.every((key) => Object.hasOwn(b, key) && equal(a[key], b[key]))
}
function snapshot(state: Snapshot): Snapshot {
  return clone({ spec: state.spec, positions: state.positions, selectedNodeId: state.selectedNodeId })
}
function sameDocument(a: Snapshot, b: Snapshot): boolean {
  return equal(a.spec, b.spec) && equal(a.positions, b.positions)
}

/** Save dialogs own these fields. Everything else must be identical between the
 * request snapshot and the result; otherwise a normalized response would hide field loss. */
function withoutIdentity(spec: WorkflowSpec): Record<string, unknown> {
  const rest = { ...spec } as Record<string, unknown>
  delete rest.id
  delete rest.title
  delete rest.facade
  return rest
}
function identityChangedDuringSave(current: WorkflowSpec, source: WorkflowSpec, field: 'title' | 'facade'): boolean {
  return !equal(current[field], source[field])
}
function historyInitial(): HistoryState {
  return { _past: [], _future: [], _savedSpec: null, _transaction: null, _transactionDepth: 0 }
}
function initial(sessionId = 0) {
  return { sessionId, spec: null, positions: {}, selectedNodeId: null, isDirty: false, validationErrors: [], pendingEdits: {}, actionError: null, canUndo: false, canRedo: false, ...historyInitial() }
}

/** Positions live only in this editor session, not localStorage or the execution document.
 * Legacy coordinates are read but never rewritten by movement. Same-workflow refreshes
 * retain the current sidecar; different workflows start from their own layout. */
function positionsFor(spec: WorkflowSpec, previous?: Snapshot): Positions {
  const layout = computeDagLayout(spec.nodes)
  return Object.fromEntries(spec.nodes.map((node) => {
    const retained = previous?.spec?.id === spec.id && Object.hasOwn(previous.positions, node.id) ? previous.positions[node.id] : undefined
    const legacy = Number.isFinite(node._x) && Number.isFinite(node._y) ? { x: node._x as number, y: node._y as number } : undefined
    return [node.id, clone(retained ?? legacy ?? layout.get(node.id) ?? { x: 80, y: 80 })]
  }))
}

export const useEditorStore = create<StoreState>((set, get) => {
  const fail = (message: string) => { set({ actionError: message }); return false }
  const ready = () => !Object.keys(get().pendingEdits).length || fail('有未应用草稿，请先应用或放弃: ' + Object.values(get().pendingEdits).join('; '))
  const active = () => ready() && (!!get().spec || fail('尚未加载工作流'))
  const nodeExists = (id: string) => !!get().spec?.nodes.some((node) => node.id === id) || fail(`节点不存在: ${id}`)

  function commit(next: Snapshot) {
    const state = get()
    if (sameDocument(state, next)) { set({ actionError: null }); return }
    const past = state._transaction ? state._past : [...state._past, snapshot(state)].slice(-HISTORY_LIMIT)
    set({ ...next, _past: past, _future: [], canUndo: past.length > 0, canRedo: false,
      isDirty: !equal(next.spec, state._savedSpec), actionError: null,
      validationErrors: next.spec ? validateSpec(next.spec) : [] })
  }

  function edit(spec: WorkflowSpec, positions = get().positions, selectedNodeId = get().selectedNodeId) {
    const before = get().spec!
    const referenceError = branchEditError(before, spec) ?? graphMutationError(before, spec)
    if (referenceError) { fail(referenceError); return }
    const previousErrors = new Set(validateSpec(before).map((error) => JSON.stringify(error)))
    const introduced = validateSpec(spec).find((error) => !['missing-field', 'missing-config'].includes(error.type) && !previousErrors.has(JSON.stringify(error)))
    if (introduced) { fail(introduced.message); return }
    commit({ spec: clone(spec), positions: clone(positions), selectedNodeId })
  }

  function finishTransaction() {
    const state = get()
    if (!state._transaction) return
    const changed = !sameDocument(state, state._transaction)
    const past = changed ? [...state._past, state._transaction].slice(-HISTORY_LIMIT) : state._past
    set({ _past: past, _transaction: null, _transactionDepth: 0, canUndo: past.length > 0 })
  }

  return {
    ...initial(),
    loadSpec: (incoming) => {
      if (!ready()) return
      const state = get()
      // A same-ID refetch can still be a different source/version. Never overwrite
      // edits just because workflow identity is unchanged.
      if (state.isDirty) { fail('当前工作流有未保存修改，不能加载其他来源；请先保存或显式放弃'); return }
      if (validateSpec(incoming).some((error) => error.type === 'invalid-structure')) { fail('工作流结构无效，未替换当前文档'); return }
      const spec = clone(incoming)
      set({ ...initial(state.sessionId + 1), spec, positions: positionsFor(spec, state), _savedSpec: clone(spec), validationErrors: validateSpec(spec) })
    },
    createNew: (id, title) => {
      if (!ready()) return
      if (get().isDirty) { fail('当前工作流有未保存修改，不能新建覆盖'); return }
      const spec: WorkflowSpec = { id, version: '1.0.0', title, nodes: [] }
      set({ ...initial(get().sessionId + 1), spec, isDirty: true, validationErrors: validateSpec(spec) })
    },
    updateSpecField: (field, value) => {
      if (!active()) return
      if (field === 'nodes') { fail('请通过节点操作或显式导入修改 nodes'); return }
      edit({ ...get().spec!, [field]: clone(value) })
    },
    addNode: (input) => {
      if (!active()) return
      const state = get()
      if (!input.id || state.spec!.nodes.some((node) => node.id === input.id)) { fail(`节点 ID 为空或已存在: ${input.id}`); return }
      const { _x, _y, ...node } = clone(input)
      const spec = { ...state.spec!, nodes: [...state.spec!.nodes, node] }
      const position = Number.isFinite(_x) && Number.isFinite(_y) ? { x: _x!, y: _y! } : computeDagLayout(spec.nodes).get(node.id)!
      edit(spec, { ...state.positions, [node.id]: position })
    },
    updateNode: (id, updates) => {
      if (!active() || !nodeExists(id)) return
      if (Object.hasOwn(updates, 'id') && updates.id !== id) { fail('节点 ID 必须保持稳定，不能通过 updateNode 修改'); return }
      if (Object.hasOwn(updates, '_x') || Object.hasOwn(updates, '_y')) { fail('请通过 moveNode 更新画布坐标'); return }
      const spec = get().spec!
      edit({ ...spec, nodes: spec.nodes.map((node) => node.id === id ? { ...node, ...clone(updates) } : node) })
    },
    removeNode: (id) => {
      if (!active() || !nodeExists(id)) return
      const state = get()
      const refs = findNodeReferences(state.spec!, id)
      if (refs.length) { fail(`不能删除 ${id}，仍有引用: ${refs.join(', ')}`); return }
      const nodes = state.spec!.nodes.filter((node) => node.id !== id).map((node) => dependencies(node).includes(id)
        ? { ...node, dependsOn: dependencies(node).filter((dep) => dep !== id) } : node)
      const positions = { ...state.positions }; delete positions[id]
      edit({ ...state.spec!, nodes }, positions, state.selectedNodeId === id ? null : state.selectedNodeId)
    },
    duplicateNode: (id) => {
      if (!active() || !nodeExists(id)) return
      const state = get(), spec = state.spec!
      const source = spec.nodes.find((node) => node.id === id)!
      const branches = getResultBranches(source)
      if (branches === null && source.onResult !== undefined) { fail('旧式或未知分支配置不能安全复制，请通过完整配置显式创建'); return }
      const ids = new Set(spec.nodes.map((node) => node.id))
      let newId = `${id}_copy`, counter = 2
      while (ids.has(newId)) newId = `${id}_copy_${counter++}`
      const copy = clone(source)
      copy.id = newId
      copy.dependsOn = []
      delete copy.branchId; delete copy._x; delete copy._y
      const used = new Set(spec.nodes.flatMap((node) => [node.branchId, ...getResultBranchIds(node)]))
      if (branches?.length) {
        const fresh = branches.map((branch) => {
          let branchId = `${newId}_${branch.branchId}`, suffix = 2
          while (used.has(branchId)) branchId = `${newId}_${branch.branchId}_${suffix++}`
          used.add(branchId)
          return { ...clone(branch), branchId }
        })
        Object.assign(copy, withResultBranches(copy, fresh))
      }
      const point = state.positions[id] ?? { x: 80, y: 80 }
      edit({ ...spec, nodes: [...spec.nodes, copy] }, { ...state.positions, [newId]: { x: point.x + 40, y: point.y + 40 } }, newId)
    },
    moveNode: (id, x, y) => {
      if (!active() || !nodeExists(id)) return
      if (!Number.isFinite(x) || !Number.isFinite(y)) { fail('画布坐标必须是有限数值'); return }
      const state = get()
      commit({ spec: state.spec, positions: { ...state.positions, [id]: { x, y } }, selectedNodeId: state.selectedNodeId })
    },
    addEdge: (source, target, branchId) => {
      if (!active()) return
      const spec = get().spec!, error = canConnect(spec, source, target, branchId)
      if (error) { fail(error); return }
      edit({ ...spec, nodes: spec.nodes.map((node) => node.id === target
        ? { ...node, dependsOn: [...dependencies(node), source], ...(branchId !== undefined ? { branchId } : {}) } : node) })
    },
    removeEdge: (source, target) => {
      if (!active() || !nodeExists(source) || !nodeExists(target)) return
      const spec = get().spec!, node = spec.nodes.find((item) => item.id === target)!
      if (!dependencies(node).includes(source)) { fail('依赖边不存在'); return }
      // Keep node-scoped membership; edit() rejects loss of its last effective source path.
      edit({ ...spec, nodes: spec.nodes.map((item) => item.id === target ? { ...item, dependsOn: dependencies(item).filter((dep) => dep !== source) } : item) })
    },
    selectNode: (id) => {
      if (!ready()) return
      if (id !== null && !nodeExists(id)) return
      set({ selectedNodeId: id, actionError: null })
    },
    setValidationErrors: (errors) => set({ validationErrors: clone(errors) }),
    setPendingEdit: (key, message) => {
      const pendingEdits = { ...get().pendingEdits }
      if (message === null) delete pendingEdits[key]
      else Object.defineProperty(pendingEdits, key, { value: message, enumerable: true, configurable: true, writable: true })
      set({ pendingEdits })
    },
    setActionError: (actionError) => set({ actionError }),
    beginTransaction: () => {
      if (!active()) return
      const state = get()
      set({ _transaction: state._transaction ?? snapshot(state), _transactionDepth: state._transactionDepth + 1, actionError: null })
    },
    endTransaction: () => {
      const state = get()
      if (state._transactionDepth > 1) set({ _transactionDepth: state._transactionDepth - 1 })
      else finishTransaction()
    },
    undo: () => {
      if (!ready()) return
      finishTransaction()
      const state = get(), previous = state._past.at(-1)
      if (!previous) { set({ actionError: null }); return }
      const past = state._past.slice(0, -1)
      set({ ...snapshot(previous), _past: past, _future: [...state._future, snapshot(state)].slice(-HISTORY_LIMIT),
        canUndo: past.length > 0, canRedo: true, isDirty: !equal(previous.spec, state._savedSpec),
        validationErrors: previous.spec ? validateSpec(previous.spec) : [], actionError: null })
    },
    redo: () => {
      if (!ready()) return
      finishTransaction()
      const state = get(), next = state._future.at(-1)
      if (!next) { set({ actionError: null }); return }
      const future = state._future.slice(0, -1)
      set({ ...snapshot(next), _past: [...state._past, snapshot(state)].slice(-HISTORY_LIMIT), _future: future,
        canUndo: true, canRedo: future.length > 0, isDirty: !equal(next.spec, state._savedSpec),
        validationErrors: next.spec ? validateSpec(next.spec) : [], actionError: null })
    },
    completeSave: (savedSpec, specAtRequest, sessionId) => {
      const state = get()
      const request = clone(specAtRequest), saved = clone(savedSpec)
      if (state.sessionId !== sessionId) { fail('保存结果已过期，未修改当前编辑器会话'); return false }
      if (!state.spec) { fail('尚未加载工作流，不能完成保存'); return false }
      if (!equal(withoutIdentity(saved), withoutIdentity(request))) {
        fail('保存结果与请求时文档不一致，可能存在字段丢失，未标记为已保存')
        return false
      }
      if (state.spec.id !== request.id) { fail('保存期间工作流标识发生变化，请重新确认后再保存'); return false }

      const current = state.spec
      const spec = { ...current, id: saved.id, title: identityChangedDuringSave(current, request, 'title') ? current.title : saved.title }
      if (identityChangedDuringSave(current, request, 'facade')) {
        // Keep an edit made while the request was in flight.
      } else if (Object.hasOwn(saved, 'facade')) spec.facade = clone(saved.facade)
      else delete spec.facade

      set({ spec: clone(spec), _savedSpec: saved, isDirty: !equal(spec, saved),
        validationErrors: validateSpec(spec), actionError: null })
      return true
    },
    markClean: (savedSpec) => {
      const state = get()
      if (!state.spec) { fail('尚未加载工作流，不能标记已保存'); return }
      if (savedSpec && savedSpec.id !== state.spec.id) { fail('保存结果属于其他工作流，未修改当前保存基线'); return }
      const baseline = clone(savedSpec ?? state.spec)
      set({ _savedSpec: baseline, isDirty: !equal(state.spec, baseline), actionError: null })
    },
    importYaml: (incoming) => {
      if (!ready()) return
      if (validateSpec(incoming).some((error) => error.type === 'invalid-structure')) { fail('导入结构无效，未替换当前文档'); return }
      const spec = clone(incoming)
      commit({ spec, positions: positionsFor(spec, get()), selectedNodeId: null })
    },
    // force is reserved for a caller-confirmed discard, never automatic navigation.
    reset: (options) => {
      if (options?.force === true) { set(initial(get().sessionId + 1)); return }
      if (!ready()) return
      if (get().isDirty) { fail('当前工作流有未保存修改，不能重置；请先保存或显式放弃'); return }
      set(initial(get().sessionId + 1))
    },
  }
})
