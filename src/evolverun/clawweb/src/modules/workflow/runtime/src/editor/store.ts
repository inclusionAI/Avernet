import { create } from 'zustand'
import type { WorkflowSpec, WorkflowNode } from '../types'

const EXECUTOR_TYPES = [
  'embedded-agent',
  'action',
  'human',
  'loop-group',
  'collaboration',
  'done',
  'subagent',
  'bcs-route',
  'baas-call',
  'mcp-call',
  'cli-script',
  'subworkflow',
  'approval',
] as const

export type ExecutorType = (typeof EXECUTOR_TYPES)[number]

export interface EditorNode extends WorkflowNode {
  _x: number
  _y: number
}

export interface ValidationError {
  type: 'cycle' | 'missing-field' | 'missing-config'
  nodeId?: string
  message: string
}

interface EditorState {
  spec: WorkflowSpec | null
  isDirty: boolean
  selectedNodeId: string | null
  validationErrors: ValidationError[]

  loadSpec: (spec: WorkflowSpec) => void
  createNew: (id: string, title: string) => void
  updateSpecField: (field: string, value: unknown) => void
  addNode: (node: EditorNode) => void
  updateNode: (nodeId: string, updates: Partial<WorkflowNode>) => void
  removeNode: (nodeId: string) => void
  moveNode: (nodeId: string, x: number, y: number) => void
  addEdge: (sourceId: string, targetId: string) => void
  removeEdge: (sourceId: string, targetId: string) => void
  selectNode: (nodeId: string | null) => void
  setValidationErrors: (errors: ValidationError[]) => void
  markClean: () => void
  importYaml: (spec: WorkflowSpec) => void
  reset: () => void
}

function removeDependsOn(nodes: WorkflowNode[], sourceId: string, targetId: string): WorkflowNode[] {
  return nodes.map((n) =>
    n.id === targetId
      ? { ...n, dependsOn: (n.dependsOn ?? []).filter((d) => d !== sourceId) }
      : n,
  )
}

function addDependsOn(nodes: WorkflowNode[], sourceId: string, targetId: string): WorkflowNode[] {
  return nodes.map((n) =>
    n.id === targetId
      ? { ...n, dependsOn: [...(n.dependsOn ?? []), sourceId] }
      : n,
  )
}

function detectCycles(nodes: WorkflowNode[]): boolean {
  const graph = new Map<string, string[]>()
  for (const node of nodes) {
    graph.set(node.id, node.dependsOn ?? [])
  }

  const visited = new Set<string>()
  const inStack = new Set<string>()

  function dfs(id: string): boolean {
    if (inStack.has(id)) return true
    if (visited.has(id)) return false
    visited.add(id)
    inStack.add(id)
    const deps = graph.get(id) ?? []
    for (const dep of deps) {
      if (dfs(dep)) return true
    }
    inStack.delete(id)
    return false
  }

  for (const node of nodes) {
    if (dfs(node.id)) return true
  }
  return false
}

function validateSpec(spec: WorkflowSpec): ValidationError[] {
  const errors: ValidationError[] = []

  if (!spec.id) errors.push({ type: 'missing-field', message: 'Workflow id is required' })
  if (!spec.version) errors.push({ type: 'missing-field', message: 'Workflow version is required' })
  if (!spec.title) errors.push({ type: 'missing-field', message: 'Workflow title is required' })

  for (const node of spec.nodes) {
    if (!node.id) {
      errors.push({ type: 'missing-field', nodeId: node.id, message: `Node missing id` })
    }
    if (!node.executor?.type) {
      errors.push({ type: 'missing-config', nodeId: node.id, message: `Node "${node.id}" missing executor type` })
    }
  }

  if (detectCycles(spec.nodes)) {
    errors.push({ type: 'cycle', message: 'Dependency cycle detected' })
  }

  return errors
}

export const useEditorStore = create<EditorState>((set) => ({
  spec: null,
  isDirty: false,
  selectedNodeId: null,
  validationErrors: [],

  loadSpec: (spec) =>
    set({ spec: { ...spec, nodes: spec.nodes.map((n) => ({ ...n })) }, isDirty: false, selectedNodeId: null, validationErrors: [] }),

  createNew: (id, title) =>
    set({
      spec: { id, version: '1.0.0', title, nodes: [] },
      isDirty: true,
      selectedNodeId: null,
      validationErrors: [],
    }),

  updateSpecField: (field, value) =>
    set((state) => {
      if (!state.spec) return state
      return { spec: { ...state.spec, [field]: value }, isDirty: true }
    }),

  addNode: (node) =>
    set((state) => {
      if (!state.spec) return state
      const newSpec = { ...state.spec, nodes: [...state.spec.nodes, node as any] }
      return { spec: newSpec, isDirty: true, validationErrors: validateSpec(newSpec) }
    }),

  updateNode: (nodeId, updates) =>
    set((state) => {
      if (!state.spec) return state
      const newNodes = state.spec.nodes.map((n) =>
        n.id === nodeId ? { ...n, ...updates } : n,
      )
      const newSpec = { ...state.spec, nodes: newNodes }
      return { spec: newSpec, isDirty: true, validationErrors: validateSpec(newSpec) }
    }),

  removeNode: (nodeId) =>
    set((state) => {
      if (!state.spec) return state
      const newNodes = state.spec.nodes
        .filter((n) => n.id !== nodeId)
        .map((n) => ({
          ...n,
          dependsOn: (n.dependsOn ?? []).filter((d) => d !== nodeId),
        }))
      const newSpec = { ...state.spec, nodes: newNodes }
      const newSelected = state.selectedNodeId === nodeId ? null : state.selectedNodeId
      return { spec: newSpec, isDirty: true, selectedNodeId: newSelected, validationErrors: validateSpec(newSpec) }
    }),

  moveNode: (nodeId, x, y) =>
    set((state) => {
      if (!state.spec) return state
      const newNodes = state.spec.nodes.map((n) =>
        n.id === nodeId ? { ...n, _x: x, _y: y } : n,
      )
      return { spec: { ...state.spec, nodes: newNodes }, isDirty: true }
    }),

  addEdge: (sourceId, targetId) =>
    set((state) => {
      if (!state.spec) return state
      if (sourceId === targetId) return state
      const newNodes = addDependsOn(state.spec.nodes, sourceId, targetId)
      const newSpec = { ...state.spec, nodes: newNodes }
      return { spec: newSpec, isDirty: true, validationErrors: validateSpec(newSpec) }
    }),

  removeEdge: (sourceId, targetId) =>
    set((state) => {
      if (!state.spec) return state
      const newNodes = removeDependsOn(state.spec.nodes, sourceId, targetId)
      const newSpec = { ...state.spec, nodes: newNodes }
      return { spec: newSpec, isDirty: true, validationErrors: validateSpec(newSpec) }
    }),

  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),

  setValidationErrors: (errors) => set({ validationErrors: errors }),

  markClean: () => set({ isDirty: false }),

  importYaml: (spec) =>
    set({ spec: { ...spec, nodes: spec.nodes.map((n) => ({ ...n })) }, isDirty: true, selectedNodeId: null, validationErrors: validateSpec(spec) }),

  reset: () =>
    set({ spec: null, isDirty: false, selectedNodeId: null, validationErrors: [] }),
}))

export { EXECUTOR_TYPES }