import { beforeEach, describe, expect, it } from 'vitest'
import { useEditorStore } from '../store'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const state = () => useEditorStore.getState()
const flow: WorkflowSpec = { id: 'readonly', title: 'Visible DB draft', version: '1', nodes: [
  { id: 'a', title: 'A', phase: 'work', executor: { type: 'done' } },
  { id: 'b', title: 'B', phase: 'work', executor: { type: 'done' } },
] }
beforeEach(() => useEditorStore.setState(useEditorStore.getInitialState(), true))

describe('central editor permission guard', () => {
  it('blocks every document/layout mutation but retains navigation and source loading', () => {
    state().loadSpec(flow)
    state().updateNode('a', { title: 'Existing draft' })
    state().setEditAccess({ workflowId: flow.id, canEdit: false })
    const before = { spec: structuredClone(state().spec), positions: structuredClone(state().positions), past: state()._past }
    state().updateSpecField('title', 'Forbidden')
    state().addNode({ id: 'c', title: 'C', phase: 'work', executor: { type: 'done' } })
    state().updateNode('a', { title: 'Forbidden' })
    state().removeNode('a'); state().duplicateNode('a'); state().moveNode('a', 900, 900)
    state().addEdge('a', 'b'); state().removeEdge('a', 'b')
    state().importYaml({ ...flow, title: 'Forbidden' })
    state().undo(); state().redo(); state().beginTransaction(); state().endTransaction()
    state().setPendingEdit('yaml', 'Forbidden')
    expect(state().spec).toEqual(before.spec)
    expect(state().positions).toEqual(before.positions)
    expect(state()._past).toBe(before.past)
    expect(state().pendingEdits).toEqual({})
    state().selectNode('a'); expect(state().selectedNodeId).toBe('a')
    state().markClean()
    state().loadSpec(flow); expect(state().spec).toEqual(flow)
    state().updateNode('a', { title: 'Still forbidden' }); expect(state().spec).toEqual(flow)
  })
  it('does not reuse edit grants across loaded workflows or restrict a local new workflow', () => {
    state().loadSpec(flow)
    state().setEditAccess({ workflowId: flow.id, canEdit: true })
    state().updateSpecField('title', 'Allowed'); expect(state().spec?.title).toBe('Allowed')
    state().markClean()
    state().loadSpec({ ...flow, id: 'other' })
    state().updateSpecField('title', 'Forbidden'); expect(state().spec?.title).toBe(flow.title)
    state().createNew('new-local', 'New')
    state().updateSpecField('title', 'Editable local draft'); expect(state().spec?.title).toBe('Editable local draft')
  })
})
