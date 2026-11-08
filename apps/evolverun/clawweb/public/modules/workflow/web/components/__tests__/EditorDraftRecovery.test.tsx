import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ validate: vi.fn(), history: vi.fn(), saved: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { validate: mocks.validate, getHistory: mocks.history } } }))
vi.mock('../../api/hooks', () => ({
  useDbWorkflow: () => ({ data: undefined, isLoading: false }),
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saved, isPending: false }),
  useAutoHealSave: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
}))
// Only unrelated surfaces are stubbed. Editor, settings, JSON/YAML and store are real.
vi.mock('../EditorCanvas', () => ({ default: () => <div>Canvas</div> }))
vi.mock('../NodePalette', () => ({ default: () => null }))
vi.mock('../NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../WorkflowHistoryPanel', () => ({ default: () => null }))
import Editor from '../../pages/Editor'
import { useEditorStore } from '../../editor/store'

const state = () => useEditorStore.getState()
const original: WorkflowSpec = { id: 'same-id', version: '1', title: 'Original', input: { keep: 1 }, nodes: [] }
const jsonDraft = '{\n  "keep": 1, "enabled": false, "values": [0, null, ""]\n}\n'
const yamlDraft = '# preserve my comment and formatting\nid: same-id\nversion: "1"\ntitle: Draft\ninput: {keep: 1}\nnodes: []\ncustom: false\n'
const change = (label: string, text: string) => fireEvent.change(screen.getByLabelText(label), { target: { value: text } })
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name, exact: true }))

function Navigation() {
  const navigate = useNavigate()
  return <>
    <NavLink to="/outside">Top-level outside</NavLink><NavLink to="/editor">Top-level editor</NavLink>
    <button onClick={() => navigate(-1)}>Back</button><button onClick={() => navigate(1)}>Forward</button>
    <Routes>
      <Route path="/editor" element={<Editor embedded initialWorkflowId={original.id} />} />
      <Route path="/outside" element={<h1>Outside editor</h1>} />
    </Routes>
  </>
}
async function mount() {
  state().loadSpec(original)
  await act(async () => { render(<MemoryRouter initialEntries={['/outside', '/editor']} initialIndex={1}><Navigation /></MemoryRouter>) })
}
function leave(kind: 'POP' | 'PUSH') {
  if (kind === 'POP') click('Back')
  else fireEvent.click(screen.getByRole('link', { name: 'Top-level outside' }))
  expect(screen.getByRole('heading', { name: 'Outside editor' })).toBeVisible()
  expect(screen.queryByLabelText('工作流 YAML')).not.toBeInTheDocument()
}
async function enter(kind: 'POP' | 'PUSH') {
  await act(async () => {
    if (kind === 'POP') click('Forward')
    else fireEvent.click(screen.getByRole('link', { name: 'Top-level editor' }))
  })
}
function deferredValidation() {
  let resolve!: (value: unknown) => void
  let reject!: (error: Error) => void
  mocks.validate.mockReturnValueOnce(new Promise((res, rej) => { resolve = res; reject = rej }))
  return { resolve, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
  state().reset({ force: true })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.validate.mockResolvedValue({ valid: true })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.spyOn(Storage.prototype, 'setItem')
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe.each(['POP', 'PUSH'] as const)('real Editor draft recovery after %s navigation', kind => {
  it.each(['valid', 'invalid'] as const)('restores complete %s JSON, opens settings, and permits apply/discard without a pending lock', async validity => {
    await mount(); click('流程设置')
    const text = validity === 'valid' ? jsonDraft : '{\n  "unfinished": '
    change('工作流输入', text)
    const oldInput = screen.getByLabelText('工作流输入')
    const pending = { ...state().pendingEdits }
    const session = state().sessionId
    leave(kind)
    expect(state().pendingEdits).toEqual(pending)
    expect(state().spec).toEqual(original)
    await enter(kind)
    expect(screen.getByRole('complementary', { name: '流程设置' })).toBeVisible()
    expect(screen.getByLabelText('工作流输入')).not.toBe(oldInput)
    expect(screen.getByLabelText('工作流输入')).toBeVisible()
    expect(screen.getByLabelText('工作流输入')).toHaveValue(text)
    expect(screen.getByText(/已保留当前会话中未应用的草稿/)).toBeVisible()
    expect(state().sessionId).toBe(session)
    expect(state().pendingEdits).toEqual(pending)
    if (validity === 'invalid') {
      expect(screen.getByRole('button', { name: '应用 工作流输入' })).toBeDisabled()
      click('放弃 工作流输入')
      expect(state().spec).toEqual(original)
    } else {
      click('应用 工作流输入')
      expect(state().spec!.input).toEqual(JSON.parse(text))
    }
    expect(state().pendingEdits).toEqual({})
    expect(screen.queryByText(/已保留当前会话中未应用的草稿/)).not.toBeInTheDocument()
    // No completed/discarded draft may return on the next mount.
    leave(kind); await enter(kind); click('流程设置')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(JSON.stringify(state().spec!.input, null, 2))
    expect(screen.getByRole('button', { name: '放弃 工作流输入' })).toBeDisabled()
    expect(state().pendingEdits).toEqual({})
    expect(window.confirm).not.toHaveBeenCalled()
    expect(Storage.prototype.setItem).not.toHaveBeenCalled()
  })

  it.each(['valid', 'invalid'] as const)('restores complete %s YAML, allows switching views, and permits apply/discard', async validity => {
    await mount(); click('YAML')
    const text = validity === 'valid' ? yamlDraft : '# keep this invalid draft\ninput: [\n'
    change('工作流 YAML', text)
    const oldInput = screen.getByLabelText('工作流 YAML')
    const pending = { ...state().pendingEdits }
    leave(kind)
    expect(state().pendingEdits).toEqual(pending)
    await enter(kind); click('YAML')
    expect(screen.getByLabelText('工作流 YAML')).not.toBe(oldInput)
    expect(screen.getByLabelText('工作流 YAML')).toBeVisible()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(text)
    expect(state().spec).toEqual(original)
    if (validity === 'invalid') {
      expect(screen.getByRole('alert')).toBeVisible()
      expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
      click('放弃 YAML 草稿')
      expect(state().spec).toEqual(original)
      expect(mocks.validate).not.toHaveBeenCalled()
    } else {
      click('校验并应用')
      await waitFor(() => expect(state().spec!.title).toBe('Draft'))
      expect(state().spec).toHaveProperty('custom', false)
      expect(mocks.validate).toHaveBeenCalledOnce()
    }
    expect(state().pendingEdits).toEqual({})
    leave(kind); await enter(kind); click('YAML')
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(stringify(state().spec, { lineWidth: 0 }))
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '放弃 YAML 草稿' })).not.toBeInTheDocument()
    expect(window.confirm).not.toHaveBeenCalled()
    expect(Storage.prototype.setItem).not.toHaveBeenCalled()
  })
})

describe('session and validation ownership across actual route remounts', () => {
  it('clears both caches while unmounted and never restores same-ID drafts in a new session', async () => {
    await mount(); click('流程设置'); change('工作流输入', jsonDraft)
    click('YAML'); change('工作流 YAML', yamlDraft)
    leave('POP')
    act(() => { state().reset({ force: true }); state().loadSpec(original) })
    await enter('POP'); click('流程设置')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(JSON.stringify(original.input, null, 2))
    click('YAML')
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(stringify(original, { lineWidth: 0 }))
    expect(state().pendingEdits).toEqual({})
    expect(screen.queryByText(/已保留当前会话中未应用的草稿/)).not.toBeInTheDocument()
  })

  it('remounts a JSON field on session change even with identical workflow and field IDs', async () => {
    await mount(); click('流程设置'); change('工作流输入', jsonDraft)
    const oldInput = screen.getByLabelText('工作流输入')
    act(() => { state().reset({ force: true }); state().loadSpec(original) })
    expect(screen.getByLabelText('工作流输入')).not.toBe(oldInput)
    expect(screen.getByLabelText('工作流输入')).toHaveValue(JSON.stringify(original.input, null, 2))
    expect(screen.getByRole('button', { name: '应用 工作流输入' })).toBeDisabled()
    expect(state().pendingEdits).toEqual({})
  })

  it('keeps both pending owners recoverable until one is discarded and the other applied', async () => {
    await mount(); click('流程设置'); change('工作流输入', jsonDraft)
    click('YAML'); change('工作流 YAML', yamlDraft)
    leave('PUSH'); await enter('PUSH')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(jsonDraft)
    click('应用 工作流输入')
    expect(state().pendingEdits).toHaveProperty('workflow:input')
    expect(state().pendingEdits).toHaveProperty('yaml')
    click('放弃 工作流输入'); click('YAML'); click('校验并应用')
    await waitFor(() => expect(state().spec!.title).toBe('Draft'))
    expect(state().pendingEdits).toEqual({})
  })

  it.each(['same', 'new'].flatMap(session => ['success', 'normalization', 'invalid', 'rejection'].map(outcome => ({ session, outcome }))))(
    'ignores late $outcome validation after unmount in a $session session', async ({ session, outcome }) => {
    await mount(); click('YAML'); change('工作流 YAML', yamlDraft)
    const stale = deferredValidation()
    click('校验并应用'); leave('PUSH')
    if (session === 'new') act(() => { state().reset({ force: true }); state().loadSpec(original) })
    await enter('PUSH'); click('YAML')
    if (session === 'same') expect(screen.getByLabelText('工作流 YAML')).toHaveValue(yamlDraft)
    const freshText = yamlDraft.replace('title: Draft', 'title: Fresh')
    change('工作流 YAML', freshText)
    const fresh = deferredValidation()
    click('校验并应用')
    await act(async () => {
      if (outcome === 'rejection') stale.reject(new Error('Old request failed'))
      else if (outcome === 'normalization') stale.resolve({ valid: true, normalizedSpec: { ...original, title: 'Stale' } })
      else if (outcome === 'invalid') stale.resolve({ valid: false, issues: [{ path: 'title', message: 'Old invalid draft' }] })
      else stale.resolve({ valid: true })
    })
    expect(state().spec).toEqual(original)
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(freshText)
    expect(screen.getByRole('button', { name: '校验中…' })).toBeDisabled()
    expect(screen.queryByText(/服务端规范化将改变/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(state().pendingEdits).toHaveProperty('yaml')
    await act(async () => { fresh.resolve({ valid: true }) })
    expect(state().spec!.title).toBe('Fresh')
    expect(state().pendingEdits).toEqual({})
  })
})


describe('restored baseline and owner metadata', () => {
  it('retains the JSON owner even when a different workflow has the same field value', async () => {
    await mount(); click('流程设置'); change('工作流输入', jsonDraft)
    leave('PUSH')
    act(() => useEditorStore.setState({ spec: { ...original, id: 'different-owner' } }))
    await enter('PUSH')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(jsonDraft)
    expect(screen.getByRole('button', { name: '应用 工作流输入' })).toBeDisabled()
    click('放弃 工作流输入')
    expect(state().spec!.id).toBe('different-owner')
    expect(state().pendingEdits).toEqual({})
  })

  it('keeps the original JSON baseline when the external field changes while unmounted', async () => {
    await mount(); click('流程设置'); change('工作流输入', jsonDraft)
    leave('POP')
    // Simulate an external owner updating the spec, not a user edit through the guarded store.
    act(() => useEditorStore.setState({ spec: { ...original, input: { external: true } } }))
    await enter('POP')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(jsonDraft)
    expect(screen.getByText(/原始配置已变化/)).toBeVisible()
    expect(screen.getByRole('button', { name: '应用 工作流输入' })).toBeDisabled()
    click('放弃 工作流输入')
    expect(screen.getByLabelText('工作流输入')).toHaveValue(JSON.stringify({ external: true }, null, 2))
    expect(state().pendingEdits).toEqual({})
  })

  it('restores YAML conflict warnings against the original baseline after unmount', async () => {
    await mount(); click('YAML'); change('工作流 YAML', yamlDraft)
    leave('PUSH')
    act(() => useEditorStore.setState({ spec: { ...original, title: 'External update' } }))
    await enter('PUSH'); click('YAML')
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(yamlDraft)
    expect(screen.getByText(/画布配置已变化/)).toBeVisible()
    click('放弃 YAML 草稿')
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(stringify(state().spec, { lineWidth: 0 }))
    expect(state().pendingEdits).toEqual({})
  })

  it('never adopts a new workflow owner for an old YAML draft after unmount', async () => {
    await mount(); click('YAML'); change('工作流 YAML', yamlDraft)
    leave('PUSH')
    act(() => useEditorStore.setState({ spec: { ...original, id: 'different-owner' } }))
    await enter('PUSH'); click('YAML')
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(yamlDraft)
    click('校验并应用')
    expect(screen.getByRole('alert')).toHaveTextContent('工作流已切换')
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(state().spec!.id).toBe('different-owner')
    click('放弃 YAML 草稿')
    expect(state().pendingEdits).toEqual({})
  })
})
