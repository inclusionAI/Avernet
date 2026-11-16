import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useEditorStore } from '../../editor/store'
import { workflowSpecSignature } from '../../services/workflow-ai-draft'
import { useEditorAiIntegration } from '../useEditorAiIntegration'

vi.mock('../../components/WorkflowAiDrawer', () => ({ default: () => null }))
const source = { id: 'ai-permissions', title: 'Original', version: '1', nodes: [] }
const state = () => useEditorStore.getState()
beforeEach(() => { useEditorStore.setState(useEditorStore.getInitialState(), true); state().loadSpec(source) })
afterEach(() => { cleanup(); useEditorStore.setState(useEditorStore.getInitialState(), true); vi.restoreAllMocks() })
function setup(canEdit: boolean) {
  const onMessage = vi.fn(), onApplied = vi.fn(), saveInFlight = { current: false }
  const hook = renderHook(({ canEdit }) => useEditorAiIntegration({
    spec: source, selectedWorkflowId: source.id, targetWorkflowId: source.id, editorSessionId: state().sessionId,
    canEdit, loading: false, saving: false, saveInFlight, hasPendingEdits: false, onMessage, onApplied,
  }), { initialProps: { canEdit } })
  return { hook, onMessage, onApplied }
}
function draft() {
  return { workflowId: source.id, baseSignature: workflowSpecSignature(source), spec: { ...source, title: 'AI draft' },
    changes: [], metadataChanges: ['title'], removedNodeIds: [], warnings: [], yaml: '', summary: '' }
}

it('does not open AI without edit permission even if a callback bypasses the disabled controls', () => {
  const { hook } = setup(false)
  expect(hook.result.current.controls.props.disabled).toBe(true)
  act(() => hook.result.current.controls.props.onOpen('modify'))
  expect(hook.result.current.open).toBe(false)
  expect(hook.result.current.drawer).toBeNull()
  expect(state().spec).toEqual(source)
})

it('rejects a stale open callback after revocation using live permissions', () => {
  const { hook } = setup(true), oldOpen = hook.result.current.controls.props.onOpen
  hook.rerender({ canEdit: false })
  act(() => oldOpen('modify'))
  expect(hook.result.current.open).toBe(false)
  expect(hook.result.current.drawer).toBeNull()
})

it('closes AI on revocation and does not revive old apply callbacks after access is regranted', () => {
  const { hook, onApplied, onMessage } = setup(true)
  act(() => hook.result.current.controls.props.onOpen('modify'))
  const apply = hook.result.current.drawer!.props.onApply
  const importYaml = vi.fn(state().importYaml)
  useEditorStore.setState({ importYaml })
  hook.rerender({ canEdit: false })
  expect(hook.result.current.open).toBe(false)
  expect(hook.result.current.drawer).toBeNull()
  act(() => apply(draft()))
  hook.rerender({ canEdit: true })
  act(() => apply(draft()))
  expect(importYaml).not.toHaveBeenCalled()
  expect(onApplied).not.toHaveBeenCalled()
  expect(onMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'success' }))
  expect(state().spec).toEqual(source)
})

it('also honors the central store denial before it can apply an otherwise current draft', () => {
  const { hook, onApplied } = setup(true)
  act(() => hook.result.current.controls.props.onOpen('modify'))
  const apply = hook.result.current.drawer!.props.onApply
  act(() => state().setEditAccess({ workflowId: source.id, canEdit: false }))
  const importYaml = vi.fn(state().importYaml)
  useEditorStore.setState({ importYaml })
  act(() => apply(draft()))
  expect(importYaml).not.toHaveBeenCalled()
  expect(onApplied).not.toHaveBeenCalled()
  expect(state().spec).toEqual(source)
})
