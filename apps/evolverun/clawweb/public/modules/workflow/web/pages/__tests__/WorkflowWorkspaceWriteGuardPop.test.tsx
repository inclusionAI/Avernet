import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ get: vi.fn(), fetchJson: vi.fn(), rows: [] as Array<Record<string, unknown>> }))
vi.mock('../../api/hooks', () => ({
  useWorkflowTypes: () => ({ data: mocks.rows, isLoading: false, isError: false, refetch: vi.fn() }),
  useCreateWorkflow: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'user', isAdmin: false }) }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: mocks.get } }, fetchJson: mocks.fetchJson }))
vi.mock('../../components/workflow-workspace/EditorTab', () => ({ default: () => null }))
vi.mock('../../components/workflow-workspace/OverviewTab', () => ({ default: () => null }))
vi.mock('../../components/workflow-workspace/ManagementTab', () => ({ default: () => null }))
vi.mock('../../components/workflow-workspace/EvolutionTab', () => ({ default: () => null }))
vi.mock('../Dashboard', () => ({ default: () => null }))
import WorkflowWorkspace from '../WorkflowWorkspace'
import { useEditorStore } from '../../editor/store'
import { beginEditorWorkflowWrite, finishEditorWorkflowWrite } from '../../editor/workflowWriteGuard'

const original: WorkflowSpec = { id: 'pop-source', title: 'Original', version: '1', nodes: [] }
let counter = 0, target: WorkflowSpec
function Location() { return <output data-testid="location">{useLocation().search}</output> }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
function mount() {
  window.history.replaceState({ idx: 0, key: 'target' }, '', `/workspace?workflowId=${target.id}&tab=editor`)
  window.history.pushState({ idx: 1, key: 'source' }, '', `/workspace?workflowId=${original.id}&tab=editor`)
  render(<BrowserRouter><WorkflowWorkspace /><Location /></BrowserRouter>)
}
beforeEach(() => {
  target = { id: `pop-target-${++counter}`, title: 'Target', version: '1', nodes: [] }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(original)
  useEditorStore.getState().updateSpecField('title', 'Unsaved original')
  mocks.rows = [original, target].map(spec => ({ workflow_id: spec.id, workflow_title: spec.title, run_count: 0, last_status: 'pending' }))
  mocks.get.mockReset().mockImplementation(async () => ({ id: target.id }))
  mocks.fetchJson.mockReset().mockImplementation(async () => ({ workflowId: target.id, packId: 'pack', specJson: JSON.stringify(target), draftToken: 'a'.repeat(64) }))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks(); sessionStorage.clear() })

it('rejects a browser POP when a target read is in flight before the write begins', async () => {
  const request = deferred<{ id: string }>()
  mocks.get.mockReturnValue(request.promise)
  mount(); act(() => window.history.back())
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(target.id))
  act(() => { beginEditorWorkflowWrite(target.id) })
  await act(async () => { request.resolve({ id: target.id }); await request.promise })
  expect(await screen.findByRole('alert')).toHaveTextContent('切换未完成')
  await waitFor(() => expect(window.location.search).toContain(`workflowId=${original.id}`))
  expect(useEditorStore.getState().spec?.title).toBe('Unsaved original')
  expect(useEditorStore.getState().isDirty).toBe(true)
})

it('rejects POP replay prepared before a complete write, even after the pending lock clears', async () => {
  const nativeGo = window.history.go.bind(window.history)
  let replay: (() => void) | undefined
  vi.spyOn(window.history, 'go').mockImplementation(delta => {
    if (delta === -1) { replay = () => nativeGo(delta); return }
    nativeGo(delta)
  })
  mount(); act(() => window.history.back())
  await waitFor(() => expect(replay).toBeTypeOf('function'))
  const owner = beginEditorWorkflowWrite(target.id)
  finishEditorWorkflowWrite(target.id, owner)
  act(() => replay!())
  expect(await screen.findByRole('alert')).toHaveTextContent('切换未完成')
  await waitFor(() => expect(window.location.search).toContain(`workflowId=${original.id}`))
  expect(useEditorStore.getState().spec?.title).toBe('Unsaved original')
})
