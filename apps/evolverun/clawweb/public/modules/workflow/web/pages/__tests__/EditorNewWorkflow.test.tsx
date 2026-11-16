import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ create: vi.fn(), fetchJson: vi.fn(), get: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: undefined, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('../../editor/newWorkflowSave', () => ({ createWorkflowPack: mocks.create }))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => null }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson, api: { workflows: { get: mocks.get, getHistory: vi.fn().mockResolvedValue({ history: [], active: null }) } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../../editor/store'
import Editor from '../Editor'
let sequence = 0
function newDraft() {
  useEditorStore.getState().reset({ force: true })
  mocks.fetchJson.mockRejectedValue(new Error('offline'))
  render(<MemoryRouter><Editor /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
  const id = `editor-create-${++sequence}`
  fireEvent.change(screen.getByLabelText('标识'), { target: { value: id } })
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'Original' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create' }))
  return id
}
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); vi.resetAllMocks() })
it('creates a valid initial node and retains edits made while the save is in flight', async () => {
  const id = newDraft(); let finish!: (value: unknown) => void
  mocks.create.mockReturnValue(new Promise(resolve => { finish = resolve }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  const submitted = mocks.create.mock.calls[0][0]
  expect(submitted).toMatchObject({ id, nodes: [{ id: `${id}_start`, executor: { type: 'done' } }] })
  act(() => useEditorStore.getState().updateSpecField('title', 'Later edit'))
  mocks.get.mockResolvedValue(submitted)
  mocks.fetchJson.mockResolvedValue({ workflowId: id, packId: id, specJson: JSON.stringify(submitted), draftToken: 'a'.repeat(64) })
  const databaseSpec = await readDatabaseWorkflowDraft(id)
  await act(async () => { finish({ result: { commit: 'a'.repeat(40) }, requestedSpec: submitted, databaseSpec }) })
  await waitFor(() => expect(useEditorStore.getState()._savedSpec?.title).toBe('Original'))
  expect(useEditorStore.getState().spec?.title).toBe('Later edit')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(screen.getByText(/已创建并保存为保存版本 commit/)).toBeInTheDocument()
})
it('preserves the new draft and does not mark it saved on an unknown outcome', async () => {
  const id = newDraft(); mocks.create.mockRejectedValue(new Error('结果尚未确认，请勿重试'))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/结果尚未确认/)).toBeInTheDocument()
  expect(useEditorStore.getState().spec?.id).toBe(id)
  expect(useEditorStore.getState()._savedSpec).toBeNull()
  expect(useEditorStore.getState().isDirty).toBe(true)
})
it('does not send creation when configuration edits are unapplied', async () => {
  newDraft()
  act(() => useEditorStore.getState().setPendingEdit('workflow:description', 'unfinished'))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/请先应用或放弃未完成的配置草稿/)).toBeInTheDocument()
  expect(mocks.create).not.toHaveBeenCalled()
  expect(useEditorStore.getState().pendingEdits['workflow:description']).toBe('unfinished')
})
it('does not replace another editor session when a create response arrives late', async () => {
  newDraft(); let finish!: (value: unknown) => void
  mocks.create.mockReturnValue(new Promise(resolve => { finish = resolve }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  const submitted = mocks.create.mock.calls[0][0]
  act(() => { useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec({ id: 'other-session', title: 'Other', version: '1', nodes: [] }) })
  await act(async () => { finish({ result: { commit: 'a'.repeat(40) }, requestedSpec: submitted }) })
  expect(useEditorStore.getState().spec?.id).toBe('other-session')
  expect(useEditorStore.getState()._savedSpec?.id).toBe('other-session')
  expect(screen.queryByText(/已创建并保存为保存版本 commit/)).not.toBeInTheDocument()
})
