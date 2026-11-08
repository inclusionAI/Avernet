import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ validate: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { validate: mocks.validate } } }))
import { useEditorStore } from '../../editor/store'
import YamlEditor from '../YamlEditor'

const spec: WorkflowSpec = { id: 'demo', title: '示例', version: '1', nodes: [] }
const draft = 'id: demo\ntitle: 新标题\nversion: "1"\nnodes: []\ncustom: false\n'
const change = (text = draft) => fireEvent.change(screen.getByLabelText('工作流 YAML'), { target: { value: text } })

beforeEach(() => { vi.resetAllMocks(); useEditorStore.getState().reset({ force: true }) })
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }) })
describe('YAML draft lifecycle', () => {
  it('retains YAML when another field draft blocks document import', async () => {
    useEditorStore.getState().setPendingEdit('node:a:args', 'pending')
    mocks.validate.mockResolvedValue({ valid: true })
    const onImport = vi.fn()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    expect(await screen.findByText('请先应用或放弃其他配置草稿，再应用 YAML')).toBeInTheDocument()
    expect(onImport).not.toHaveBeenCalled()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft)
  })
  it('restores its pending guard and keeps text when import is rejected', async () => {
    mocks.validate.mockResolvedValue({ valid: true })
    render(<YamlEditor spec={spec} onImport={() => { throw new Error('不能覆盖当前配置') }} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    expect(await screen.findByText('不能覆盖当前配置')).toBeInTheDocument()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft)
    expect(useEditorStore.getState().pendingEdits.yaml).toEqual(expect.any(String))
  })

  it('keeps unapplied YAML when visual document changes', () => {
    const { rerender } = render(<YamlEditor spec={spec} onImport={vi.fn()} />)
    change()
    rerender(<YamlEditor spec={{ ...spec, title: '画布更新' }} onImport={vi.fn()} />)
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft)
    expect(screen.getByText(/画布配置已变化/)).toBeInTheDocument()
  })
  it('applies validated text and clears its pending guard', async () => {
    const onImport = vi.fn()
    mocks.validate.mockResolvedValue({ valid: true })
    render(<YamlEditor spec={spec} onImport={onImport} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    await waitFor(() => expect(onImport).toHaveBeenCalledWith(expect.objectContaining({ title: '新标题', custom: false })))
    expect(useEditorStore.getState().pendingEdits).not.toHaveProperty('yaml')
  })
  it('requires explicit acceptance of normalization changes', async () => {
    const onImport = vi.fn()
    mocks.validate.mockResolvedValue({ valid: true, normalizedSpec: { ...spec, title: '新标题' } })
    render(<YamlEditor spec={spec} onImport={onImport} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    await screen.findByText(/服务端规范化将改变/)
    expect(onImport).not.toHaveBeenCalled()
    expect(screen.getByText('custom')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /确认采用规范化结果/ }))
    expect(onImport).toHaveBeenCalledWith({ ...spec, title: '新标题' })
  })
  it('never applies a validation response for an older text revision', async () => {
    let resolve: (value: unknown) => void = () => {}
    mocks.validate.mockImplementation(() => new Promise(r => { resolve = r }))
    const onImport = vi.fn()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    change(draft.replace('新标题', '更新的标题'))
    await act(async () => { resolve({ valid: true }) })
    expect(onImport).not.toHaveBeenCalled()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft.replace('新标题', '更新的标题'))
  })
  it('never applies a response after unmount', async () => {
    let resolve: (value: unknown) => void = () => {}
    mocks.validate.mockImplementation(() => new Promise(r => { resolve = r }))
    const onImport = vi.fn()
    const { unmount } = render(<YamlEditor spec={spec} onImport={onImport} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    unmount()
    await act(async () => { resolve({ valid: true }) })
    expect(onImport).not.toHaveBeenCalled()
  })
  it('reports invalid documents without calling validation', () => {
    render(<YamlEditor spec={spec} onImport={vi.fn()} />)
    change('null')
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/必须包含/)
  })
  it('reports a network failure and keeps the unapplied draft', async () => {
    mocks.validate.mockRejectedValue(new Error('网络不可用'))
    render(<YamlEditor spec={spec} onImport={vi.fn()} />)
    change()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    expect(await screen.findByText('网络不可用')).toBeInTheDocument()
    expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft)
  })
})
it('retains a rejected YAML apply across remount and allows retry without losing pending or source', async () => {
  mocks.validate.mockResolvedValue({ valid: true })
  const onImport = vi.fn().mockImplementationOnce(() => { throw new Error('写入被拒绝') })
  const view = render(<YamlEditor spec={spec} onImport={onImport} />)
  change()
  fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
  await screen.findByText('写入被拒绝')
  view.unmount()
  expect(useEditorStore.getState().pendingEdits).toHaveProperty('yaml')
  render(<YamlEditor spec={spec} onImport={onImport} />)
  expect(screen.getByLabelText('工作流 YAML')).toHaveValue(draft)
  fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
  await waitFor(() => expect(onImport).toHaveBeenCalledTimes(2))
  expect(useEditorStore.getState().pendingEdits).toEqual({})
})
