import { beforeEach, describe, expect, it } from 'vitest'
import type { WorkflowNode, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../store'
import { getResultBranches, withResultBranches } from '../contracts'
import { normalizeWorkflowSpec } from '../../../server/workflow'

const state = () => useEditorStore.getState()
const node = (id: string, patch: Record<string, unknown> = {}): WorkflowNode => ({ id, title: id, phase: 'work', executor: { type: 'done' }, ...patch }) as WorkflowNode
const flow = (nodes = [node('a'), node('b')]): WorkflowSpec => ({ id: 'flow', version: '1', title: 'Flow', nodes })
const router = () => ({ ...node('r'), ...withResultBranches(node('r'), [{ branchId: 'yes', match: { ok: true } }]) })
const raw = () => JSON.parse(JSON.stringify(state().spec))

beforeEach(() => { useEditorStore.setState(useEditorStore.getInitialState(), true) })

describe('lossless document and position boundaries', () => {
  it('deep-clones complete input, edits and saved snapshots without normalizing hidden fields', () => {
    const source = flow([node('a', {
      executor: { type: 'future-executor', nested: { values: [false, 0, null, '', 'false', '0'] } },
      retry: { custom: { enabled: false, delay: 0 } },
      outputContract: { required: ['answer'], additionalProperties: false },
      onResult: { if: { ok: false }, then: { complete: false }, unknown: null },
      knowledge: false, unknownNode: { empty: '', zero: 0, null: null },
    })])
    source.unknownTop = { flags: [false, 0, null], extension: { values: [] } }
    source.defaults = { contextPolicy: '', future: { nullable: null } }
    const expected = structuredClone(source)
    state().loadSpec(source)
    ;(source.nodes[0].executor.nested as { values: unknown[] }).values.push('MUTATED')
    ;(source.unknownTop as { flags: unknown[] }).flags.push('MUTATED')
    expect(raw()).toEqual(expected)
    state().updateNode('a', { title: 'Edited' })
    expected.nodes[0].title = 'Edited'
    expect(raw()).toEqual(expected)
    expect(normalizeWorkflowSpec(raw())).toEqual(expected)
    const patch = { params: { nested: [false, 0, null] } }
    state().updateSpecField('params', patch.params)
    patch.params.nested.push(99)
    expect(state().spec!.params).toEqual({ nested: [false, 0, null] })
    state().undo()
    expect(raw()).toEqual(expected)
    state().undo()
    expect(state().isDirty).toBe(false)
  })
  it('preserves missing version and missing phase as-is while reporting phase', () => {
    const source = flow([node('a')]); delete source.version; delete source.nodes[0].phase
    state().loadSpec(source)
    state().updateNode('a', { title: 'Legacy' })
    expect(raw()).not.toHaveProperty('version')
    expect(raw().nodes[0]).not.toHaveProperty('phase')
    expect(state().validationErrors.some((error) => error.message.includes('phase'))).toBe(true)
  })
  it('extracts legacy addNode coordinates into sidecar without writing them to the new node', () => {
    state().loadSpec(flow())
    const input = { ...node('c'), _x: 0, _y: -10 }
    state().addNode(input)
    expect(state().positions.c).toEqual({ x: 0, y: -10 })
    expect(raw().nodes[2]).not.toHaveProperty('_x')
    expect(raw().nodes[2]).not.toHaveProperty('_y')
    expect(input._x).toBe(0)
  })
  it('retains preexisting legacy fields; moving only changes session sidecar and is undoable', () => {
    const source = flow([node('a', { _x: 0, _y: 12 })])
    state().loadSpec(source)
    expect(state().positions.a).toEqual({ x: 0, y: 12 })
    const document = state().spec
    state().moveNode('a', 55, 88)
    expect(state().spec).toBe(document)
    expect(raw()).toEqual(source)
    expect(state().isDirty).toBe(false)
    expect(state().canUndo).toBe(true)
    state().undo()
    expect(state().positions.a).toEqual({ x: 0, y: 12 })
    state().redo()
    expect(state().positions.a).toEqual({ x: 55, y: 88 })
    state().loadSpec({ ...source, title: 'Refetched' })
    expect(state().positions.a).toEqual({ x: 55, y: 88 })
    expect(state().canUndo).toBe(false)
  })
  it('new workflow contains no real tasks or fabricated starter nodes', () => {
    state().createNew('empty', 'Empty')
    expect(state().spec).toMatchObject({ id: 'empty', title: 'Empty', nodes: [] })
    expect(state().positions).toEqual({})
    expect(state().isDirty).toBe(true)
  })
  it('never normalizes unknown executors to done on load/update/duplicate', () => {
    state().loadSpec(flow([node('a', { executor: { type: 'extension', deep: { value: false } } })]))
    state().updateNode('a', { title: 'Extension' })
    state().duplicateNode('a')
    expect(state().spec!.nodes.map((n) => n.executor.type)).toEqual(['extension', 'extension'])
  })
})

describe('mutation safety and actions', () => {
  it.each([
    () => state().addEdge('a', 'a'),
    () => state().addEdge('missing', 'a'),
    () => state().addEdge('a', 'missing'),
    () => state().addEdge('a', 'b'),
    () => state().addEdge('c', 'a'),
    () => state().updateNode('a', { id: 'renamed' }),
    () => state().updateNode('a', { id: undefined }),
    () => state().updateNode('a', { dependsOn: ['c'] }),
    () => state().updateNode('b', { dependsOn: ['a', 'a'] }),
    () => state().updateNode('b', { dependsOn: ['missing'] }),
    () => state().addNode(node('a')),
    () => state().addNode(node('bad', { dependsOn: ['missing'] })),
    () => state().moveNode('a', NaN, 0),
    () => state().updateNode('a', { _x: 3 }),
    () => state().updateNode('missing', { title: 'no' }),
    () => state().removeNode('missing'),
    () => state().duplicateNode('missing'),
    () => state().removeEdge('a', 'missing'),
    () => state().removeEdge('a', 'c'),
    () => state().updateSpecField('nodes', []),
  ])('rejects unsafe mutation without recording history (%#)', (action) => {
    const source = flow([node('a'), node('b', { dependsOn: ['a'] }), node('c', { dependsOn: ['b'] })])
    state().loadSpec(source)
    action()
    expect(raw()).toEqual(source)
    expect(state().actionError).toBeTruthy()
    expect(state().canUndo).toBe(false)
    expect(state().isDirty).toBe(false)
    state().setActionError(null)
    expect(state().actionError).toBeNull()
  })
  it('adds and removes edges with history; preserves unrelated missing properties', () => {
    state().loadSpec(flow())
    state().addEdge('a', 'b')
    expect(raw().nodes[1].dependsOn).toEqual(['a'])
    state().removeEdge('a', 'b')
    expect(raw().nodes[1].dependsOn).toEqual([])
    state().undo()
    expect(raw().nodes[1].dependsOn).toEqual(['a'])
    state().removeNode('a')
    expect(raw().nodes).toEqual([node('b', { dependsOn: [] })])
    state().undo()
    state().loadSpec(flow()) // refused: the dependency remains unsaved
    expect(state().actionError).toBeTruthy()
  })
  it('deletes ordinary dependencies without materializing missing dependsOn elsewhere', () => {
    state().loadSpec(flow([node('a'), node('b', { dependsOn: ['a'] }), node('c')]))
    state().selectNode('a'); state().removeNode('a')
    expect(raw().nodes[0].dependsOn).toEqual([])
    expect(raw().nodes[1]).not.toHaveProperty('dependsOn')
    expect(state().selectedNodeId).toBeNull()
    expect(state().positions).not.toHaveProperty('a')
    state().undo()
    expect(state().selectedNodeId).toBe('a')
    expect(raw().nodes).toHaveLength(3)
  })
  it.each([
    { outputs: { x: '{{nodeOutput.a.answer}}' } },
    { nodes: [node('a'), node('b', { onFeedback: { target: 'a' } })] },
    { nodes: [node('a'), node('b', { executor: { type: 'human', actions: { revise: { target: 'a' } } } })] },
    { nodes: [node('a'), node('b', { onResult: [{ value: 'ok', target: 'a' }] })] },
  ])('blocks deletion of referenced nodes and lists the reference (%#)', (patch) => {
    const source = { ...flow(), ...patch }
    state().loadSpec(source)
    state().removeNode('a')
    expect(raw()).toEqual(source)
    expect(state().actionError).toContain('引用')
    expect(state().canUndo).toBe(false)
  })
  it.each([true, false])('keeps branch deletion and rename protection with complete=%s', (complete) => {
    const source = { ...router(), ...withResultBranches(router(), [{ branchId: 'yes', match: {}, complete }]) }
    state().loadSpec(flow([source, node('b')]))
    state().addEdge('r', 'b', 'yes')
    expect(raw().nodes[1]).toMatchObject({ dependsOn: ['r'], branchId: 'yes' })
    state().markClean()
    for (const action of [
      () => state().updateNode('r', withResultBranches(state().spec!.nodes[0], [])),
      () => state().updateNode('r', withResultBranches(state().spec!.nodes[0], [{ branchId: 'no', match: {} }])),
      () => state().removeNode('r'),
    ]) {
      action()
      expect(state().actionError).toBeTruthy()
      expect(state().isDirty).toBe(false)
      expect(getResultBranches(state().spec!.nodes[0])![0].branchId).toBe('yes')
    }
  })
  it.each([true, false])('connects with complete=%s but protects the last branch source edge without losing metadata', (complete) => {
    const branch = { branchId: 'yes', match: { ok: false }, complete, future: { zero: 0, nullable: null } }
    const source = { ...router(), ...withResultBranches(router(), [branch]) }
    state().loadSpec(flow([source, node('b')]))
    state().addEdge('r', 'b', 'yes')
    expect(state().actionError).toBeNull()
    expect(state().validationErrors).toEqual([])
    expect(raw().nodes[1]).toMatchObject({ dependsOn: ['r'], branchId: 'yes' })
    expect(getResultBranches(state().spec!.nodes[0])).toEqual([branch])
    state().undo()
    expect(raw().nodes[1]).not.toHaveProperty('branchId')
    state().redo()
    state().removeEdge('r', 'b')
    expect(state().actionError).toBeTruthy()
    expect(raw().nodes[1]).toMatchObject({ dependsOn: ['r'], branchId: 'yes' })
    expect(getResultBranches(state().spec!.nodes[0])).toEqual([branch])
    state().removeNode('r')
    expect(state().actionError).toContain('引用')
  })
  it.each([true, false])('changes a referenced branch complete to %s with undo/redo and no field loss', (complete) => {
    const branch = { branchId: 'yes', match: { ok: false }, complete: !complete, future: { zero: 0, nullable: null } }
    const source = { ...router(), ...withResultBranches(router(), [branch]) }
    state().loadSpec(flow([source, node('b', { dependsOn: ['r'], branchId: 'yes' })]))
    expect(state().validationErrors).toEqual([])
    state().updateNode('r', withResultBranches(state().spec!.nodes[0], [{ ...branch, complete }]))
    expect(state().actionError).toBeNull()
    expect(state().validationErrors).toEqual([])
    expect(state().isDirty).toBe(true)
    expect(getResultBranches(state().spec!.nodes[0])).toEqual([{ ...branch, complete }])
    expect(raw().nodes[1]).toEqual(node('b', { dependsOn: ['r'], branchId: 'yes' }))
    state().undo()
    expect(getResultBranches(state().spec!.nodes[0])).toEqual([branch])
    expect(state().isDirty).toBe(false)
    state().redo()
    expect(getResultBranches(state().spec!.nodes[0])).toEqual([{ ...branch, complete }])
  })
  it('copies to a unique ID, clears dependencies/membership, and regenerates branch IDs', () => {
    const source = { ...router(), branchId: 'outer', dependsOn: ['root'], _x: 10, _y: 20 }
    state().loadSpec(flow([node('root'), source, node('r_copy')]))
    state().duplicateNode('r')
    const copy = state().spec!.nodes.at(-1)!
    expect(copy.id).toBe('r_copy_2')
    expect(copy.dependsOn).toEqual([])
    expect(copy).not.toHaveProperty('branchId')
    expect(copy).not.toHaveProperty('_x')
    expect(getResultBranches(copy)![0]).toMatchObject({ match: { ok: true } })
    expect(getResultBranches(copy)![0].branchId).not.toBe('yes')
    expect(state().positions[copy.id]).toEqual({ x: 50, y: 60 })
    expect(state().selectedNodeId).toBe(copy.id)
    expect(raw().nodes[1]).toEqual(source)
    state().undo()
    expect(raw().nodes).toHaveLength(3)
    state().redo()
    expect(raw().nodes.at(-1).id).toBe(copy.id)
  })
  it('does not let an existing imported cycle hide a newly introduced cycle', () => {
    state().loadSpec(flow([node('a', { dependsOn: ['b'] }), node('b', { dependsOn: ['a'] }), node('c'), node('d', { dependsOn: ['c'] })]))
    const before = raw()
    state().updateNode('c', { dependsOn: ['d'] })
    expect(state().actionError).toContain('cycle')
    expect(raw()).toEqual(before)
  })
  it('does not allow an existing duplicate dependency to hide another duplicate', () => {
    state().loadSpec(flow([node('a'), node('b', { dependsOn: ['a', 'a'] })]))
    const before = raw()
    state().updateNode('b', { dependsOn: ['a', 'a', 'a'] })
    expect(state().actionError).toBeTruthy()
    expect(raw()).toEqual(before)
  })
  it('newly imported IDs never inherit Object.prototype as a position', () => {
    state().loadSpec(flow())
    state().importYaml(flow([node('a'), node('__proto__'), node('constructor')]))
    expect(state().positions.__proto__).toEqual({ x: expect.any(Number), y: expect.any(Number) })
    expect(state().positions.constructor).toEqual({ x: expect.any(Number), y: expect.any(Number) })
  })
  it('fails explicitly rather than guessing how to duplicate unsupported routing', () => {
    state().loadSpec(flow([node('a', { onResult: [{ value: 'ok', target: 'b' }] }), node('b')]))
    state().duplicateNode('a')
    expect(state().actionError).toBeTruthy()
    expect(state().spec!.nodes).toHaveLength(2)
  })
})

describe('history, transactions and saved baseline', () => {
  it('groups a whole drag into one step, supports nesting, and ignores empty transactions', () => {
    state().loadSpec(flow())
    const original = structuredClone(state().positions)
    state().beginTransaction(); state().beginTransaction()
    for (let x = 0; x < 20; x++) state().moveNode('a', x, 20)
    state().endTransaction(); state().endTransaction()
    expect(state().canUndo).toBe(true)
    expect(state().isDirty).toBe(false)
    state().undo()
    expect(state().positions).toEqual(original)
    expect(state().canUndo).toBe(false)
    state().redo()
    expect(state().positions.a).toEqual({ x: 19, y: 20 })
    state().beginTransaction(); state().endTransaction()
    state().undo()
    expect(state().positions).toEqual(original)
    expect(state().canUndo).toBe(false)
  })
  it('closes an open transaction before undo and keeps spec and positions atomic', () => {
    state().loadSpec(flow())
    const original = structuredClone(state().positions)
    state().beginTransaction()
    state().moveNode('a', 1, 2); state().updateNode('a', { title: 'Changed' })
    state().undo()
    expect(raw()).toEqual(flow())
    expect(state().positions).toEqual(original)
    state().redo()
    expect(raw().nodes[0].title).toBe('Changed')
    expect(state().positions.a).toEqual({ x: 1, y: 2 })
  })
  it('caps history at 50 steps, and discards redo after a new edit', () => {
    state().loadSpec(flow())
    for (let i = 1; i <= 60; i++) state().updateSpecField('title', `Flow ${i}`)
    for (let i = 0; i < 50; i++) state().undo()
    expect(state().spec!.title).toBe('Flow 10')
    expect(state().canUndo).toBe(false)
    for (let i = 0; i < 50; i++) state().redo()
    expect(state().spec!.title).toBe('Flow 60')
    expect(state().canRedo).toBe(false)
    state().undo(); state().updateSpecField('title', 'New branch')
    expect(state().canRedo).toBe(false)
  })
  it('does not record selection, no-op patches or an unchanged move', () => {
    state().loadSpec(flow()); state().selectNode('a')
    state().updateNode('a', { title: 'a', id: 'a' })
    const point = state().positions.a
    state().moveNode('a', point.x, point.y)
    state().beginTransaction(); state().endTransaction()
    expect(state().canUndo).toBe(false)
    expect(state().isDirty).toBe(false)
  })
  it('does not mark newer edits saved when a prior snapshot finishes saving', () => {
    state().loadSpec(flow())
    state().updateNode('a', { title: 'Save this' })
    const saving = structuredClone(state().spec!)
    state().updateNode('a', { title: 'Newer edit' })
    state().markClean(saving)
    expect(state().isDirty).toBe(true)
    expect(state().spec!.nodes[0].title).toBe('Newer edit')
    saving.nodes[0].title = 'Mutate caller snapshot'
    state().undo()
    expect(state().spec!.nodes[0].title).toBe('Save this')
    expect(state().isDirty).toBe(false)
    state().undo()
    expect(state().isDirty).toBe(true)
    state().redo()
    expect(state().isDirty).toBe(false)
  })
  it('completes a save and adopts the new identity without keeping the document dirty', () => {
    state().loadSpec(flow())
    state().updateNode('a', { title: 'Authorized edit' })
    const request = structuredClone(state().spec!)
    const saved = { ...structuredClone(request), id: 'renamed', title: 'Saved title', facade: { command: 'saved' } }
    expect(state().completeSave(saved, request, state().sessionId)).toBe(true)
    expect(raw()).toEqual(saved)
    expect(state().isDirty).toBe(false)
    expect(state().actionError).toBeNull()
  })

  it('completes a rename while keeping edits made while the save request was in flight', () => {
    state().loadSpec(flow())
    state().updateNode('a', { title: 'Authorized edit' })
    const request = structuredClone(state().spec!)
    state().updateNode('a', { title: 'Newer edit' })
    const saved = { ...structuredClone(request), id: 'renamed', title: 'Saved title' }
    expect(state().completeSave(saved, request, state().sessionId)).toBe(true)
    expect(state().spec).toMatchObject({ id: 'renamed', title: 'Saved title' })
    expect(state().spec!.nodes[0].title).toBe('Newer edit')
    expect(state()._savedSpec).toMatchObject({ id: 'renamed', title: 'Saved title' })
    expect(state()._savedSpec!.nodes[0].title).toBe('Authorized edit')
    expect(state().isDirty).toBe(true)
    expect(state().actionError).toBeNull()
  })

  it('keeps title and facade edits made while a save was in flight', () => {
    state().loadSpec(flow())
    const request = structuredClone(state().spec!)
    state().updateSpecField('title', 'Later title')
    state().updateSpecField('facade', { command: 'later' })
    const saved = { ...structuredClone(request), id: 'renamed', title: 'Saved title' }
    expect(state().completeSave(saved, request, state().sessionId)).toBe(true)
    expect(state().spec).toMatchObject({ id: 'renamed', title: 'Later title', facade: { command: 'later' } })
    expect(state().spec).not.toHaveProperty('facade', undefined)
    expect(state().isDirty).toBe(true)
  })

  it.each([
    { description: 'stale session', sessionId: 99, patch: (spec: WorkflowSpec) => spec, error: '已过期' },
    { description: 'hidden field loss', sessionId: undefined, patch: (spec: WorkflowSpec) => { const next = structuredClone(spec); delete next.nodes[0].title; return next }, error: '字段丢失' },
    { description: 'identity changed after the request', sessionId: undefined, patch: (spec: WorkflowSpec) => ({ ...spec, id: 'changed' }), error: '标识发生变化' },
  ])('rejects save completion for %s (%#)', ({ sessionId, patch, error }) => {
    state().loadSpec(flow())
    state().updateNode('a', { title: 'Saving' })
    const request = structuredClone(state().spec!)
    if (patch && error === '标识发生变化') {
      state().updateSpecField('id', 'changed')
    }
    const savedSpec = patch ? patch({ ...structuredClone(request), id: 'renamed' }) : structuredClone(request)
    expect(state().completeSave(savedSpec, request, sessionId ?? state().sessionId)).toBe(false)
    expect(state().actionError).toContain(error)
    expect(state().spec).toMatchObject({ id: error === '标识发生变化' ? 'changed' : 'flow' })
    expect(state().spec!.nodes[0].title).toBe('Saving')
  })

  it('keeps drafts when save finishes, and protects a different workflow from stale save completion', () => {
    state().loadSpec(flow())
    const saving = state().spec!
    state().setPendingEdit('yaml', 'Not applied')
    state().markClean(saving)
    expect(state().pendingEdits).toEqual({ yaml: 'Not applied' })
    state().setPendingEdit('yaml', null)
    state().loadSpec({ ...flow(), id: 'other' })
    state().markClean(saving)
    expect(state().actionError).toBeTruthy()
    expect(state().spec!.id).toBe('other')
    expect(state().isDirty).toBe(false)
  })
  it('keeps a clean baseline across import undo/redo and clones imported input', () => {
    state().loadSpec(flow())
    const imported = { ...flow(), title: 'Imported', unknown: { zero: 0 } }
    state().importYaml(imported)
    imported.unknown.zero = 5
    expect(state().spec!.unknown).toEqual({ zero: 0 })
    expect(state().isDirty).toBe(true)
    state().undo(); expect(state().isDirty).toBe(false)
    state().redo(); expect(state().spec!.title).toBe('Imported')
  })
})

describe('pending drafts and guarded document replacement', () => {
  it.each([
    () => state().loadSpec(flow()), () => state().createNew('new', 'New'),
    () => state().importYaml(flow()), () => state().reset(),
    () => state().updateSpecField('title', 'Lose draft'),
    () => state().addNode(node('c')), () => state().updateNode('a', { title: 'Lose draft' }),
    () => state().removeNode('a'), () => state().duplicateNode('a'),
    () => state().moveNode('a', 0, 0), () => state().addEdge('b', 'a'),
    () => state().removeEdge('a', 'b'), () => state().selectNode('b'),
    () => state().selectNode(null), () => state().undo(), () => state().redo(),
    () => state().beginTransaction(),
  ])('rejects operation %# with pending draft without clearing it', (action) => {
    state().loadSpec(flow([node('a'), node('b', { dependsOn: ['a'] })]))
    state().selectNode('a')
    state().updateSpecField('title', 'Changed'); state().undo()
    state().setPendingEdit('node:a:raw', '') // Even an empty message is an active draft.
    const before = { spec: raw(), positions: structuredClone(state().positions), selected: state().selectedNodeId, canRedo: state().canRedo }
    action()
    expect(raw()).toEqual(before.spec)
    expect(state().positions).toEqual(before.positions)
    expect(state().selectedNodeId).toBe(before.selected)
    expect(state().canRedo).toBe(before.canRedo)
    expect(state().pendingEdits).toEqual({ 'node:a:raw': '' })
    expect(state().actionError).toContain('草稿')
  })
  it('permits explicit apply only after every pending key is cleared', () => {
    state().loadSpec(flow())
    state().setPendingEdit('yaml', 'YAML'); state().setPendingEdit('node:a:raw', 'JSON')
    state().setPendingEdit('node:a:raw', null)
    state().updateNode('a', { title: 'Still blocked' })
    expect(state().spec!.nodes[0].title).toBe('a')
    state().setPendingEdit('yaml', null)
    state().updateNode('a', { title: 'Applied' })
    expect(state().spec!.nodes[0].title).toBe('Applied')
    expect(state().actionError).toBeNull()
  })
  it('loads initially and rejects a same-workflow new source while dirty', () => {
    state().loadSpec(flow()); expect(state().spec).not.toBeNull()
    state().updateSpecField('title', 'Unsaved')
    state().loadSpec({ ...flow(), title: 'New server source' })
    expect(state().spec!.title).toBe('Unsaved')
    expect(state().actionError).toBeTruthy()
    expect(state().canUndo).toBe(true)
    state().undo(); state().loadSpec({ ...flow(), title: 'New server source' })
    expect(state().spec!.title).toBe('New server source')
    expect(state().canUndo).toBe(false)
    expect(state().canRedo).toBe(false)
  })
  it.each([() => state().reset(), () => state().createNew('new', 'New')])('guards unsaved document replacement (%#)', (action) => {
    state().loadSpec(flow()); state().updateSpecField('title', 'Unsaved'); action()
    expect(state().spec!.title).toBe('Unsaved')
    expect(state().isDirty).toBe(true)
    expect(state().actionError).toBeTruthy()
  })
  it('allows reset({force:true}) only as an explicit confirmed discard path', () => {
    state().loadSpec(flow()); state().updateSpecField('title', 'Unsaved')
    state().setPendingEdit('yaml', 'Draft')
    state().beginTransaction()
    state().reset({ force: true })
    expect(state().spec).toBeNull()
    expect(state().pendingEdits).toEqual({})
    expect(state().positions).toEqual({})
    expect(state().actionError).toBeNull()
    expect(state().canUndo).toBe(false)
    expect(state().canRedo).toBe(false)
    expect(state().isDirty).toBe(false)
    state().loadSpec({ ...flow(), id: 'other' })
    expect(state().spec!.id).toBe('other')
  })
  it('rejects malformed replacement instead of announcing successful load/import', () => {
    state().loadSpec(flow())
    const bad = { ...flow(), nodes: null }
    state().importYaml(bad)
    expect(raw()).toEqual(flow()); expect(state().actionError).toBeTruthy()
    state().loadSpec(bad)
    expect(raw()).toEqual(flow()); expect(state().actionError).toBeTruthy()
  })
  it('allows imported cycles for diagnosis/rendering and then undo', () => {
    state().loadSpec(flow())
    state().importYaml(flow([node('a', { dependsOn: ['b'] }), node('b', { dependsOn: ['a'] })]))
    expect(state().validationErrors.some((error) => error.type === 'cycle')).toBe(true)
    expect(Object.keys(state().positions)).toHaveLength(2)
    state().updateNode('a', { title: 'Can still inspect/edit legacy data' })
    expect(state().actionError).toBeNull()
    state().undo(); state().undo()
    expect(raw()).toEqual(flow())
  })
})

describe('editing session lifecycle', () => {
  it('advances monotonically on successful load, reset and createNew, even with the same workflow ID', () => {
    let session = state().sessionId
    expect(Number.isInteger(session)).toBe(true)
    for (const action of [
      () => state().loadSpec(flow()),
      () => state().loadSpec(flow()),
      () => state().reset(),
      () => state().reset({ force: true }),
      () => state().loadSpec(flow()),
      () => state().createNew('flow', 'New document'),
      () => state().reset({ force: true }),
    ]) {
      action()
      expect(state().sessionId).toBe(session + 1)
      session = state().sessionId
    }
  })

  it('does not advance for rejected document replacements or pending guards', () => {
    state().loadSpec(flow())
    const session = state().sessionId
    state().setPendingEdit('yaml', 'Draft')
    for (const action of [() => state().loadSpec(flow()), () => state().createNew('new', 'New'), () => state().reset()]) {
      action()
      expect(state().sessionId).toBe(session)
      expect(state().pendingEdits).toHaveProperty('yaml')
    }
    state().setPendingEdit('yaml', null)
    state().updateSpecField('title', 'Unsaved')
    state().loadSpec(flow()); state().createNew('new', 'New'); state().reset()
    expect(state().sessionId).toBe(session)
    state().markClean()
    state().loadSpec({ ...flow(), nodes: null })
    expect(state().sessionId).toBe(session)
  })

  it('keeps session identity outside spec/history and unchanged by editing, import or save completion', () => {
    state().loadSpec(flow())
    const session = state().sessionId
    state().selectNode('a')
    state().beginTransaction(); state().moveNode('a', 1, 2); state().endTransaction()
    state().updateNode('a', { title: 'Saving' })
    const saved = structuredClone(state().spec!)
    state().updateNode('a', { title: 'Newer edit' })
    state().markClean(saved)
    expect(state().isDirty).toBe(true)
    state().undo(); expect(state().isDirty).toBe(false)
    state().redo(); expect(state().isDirty).toBe(true)
    state().importYaml({ ...flow(), title: 'Imported' })
    state().undo(); state().redo()
    state().setPendingEdit('yaml', 'Draft'); state().markClean()
    expect(state().sessionId).toBe(session)
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(state().spec).not.toHaveProperty('sessionId')
  })
})
