import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ validate: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { validate: mocks.validate } } }))
import { readDraft } from '../../editor/draftCache'
import { useEditorStore } from '../../editor/store'
import { parseWorkflowYaml } from '../../editor/yamlDraft'
import YamlEditor from '../YamlEditor'

const spec: WorkflowSpec = { id: 'demo', title: '示例', version: '1', nodes: [] }
const draft = '# 原样保留 <img src=x>\nid: demo\ntitle: "新的标题"  # spacing\nversion: "1"\n\nnodes: []\ncustom: false\n\n'
const state = () => useEditorStore.getState()
const input = () => screen.getByRole('textbox', { name: '工作流 YAML' }) as HTMLTextAreaElement
const highlight = () => screen.getByTestId('pack-file-highlight')
const tokens = (kind: string) => Array.from(highlight().querySelectorAll(`[data-syntax-kind="${kind}"]`), node => node.textContent)

beforeEach(() => { vi.resetAllMocks(); state().reset({ force: true }); state().loadSpec(spec) })
afterEach(() => { cleanup(); vi.restoreAllMocks(); state().reset({ force: true }) })

describe('real YAML configuration editor highlighting', () => {
  it('highlights initial keys and quoted strings using the shared editor with a shrinking flex layout', () => {
    const onImport = vi.fn()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    expect(input()).toHaveValue(stringify(spec, { lineWidth: 0 }))
    expect(tokens('key')).toEqual(expect.arrayContaining(['id', 'version', 'nodes']))
    expect(tokens('string')).toContain('"1"')
    expect(highlight()).toHaveAttribute('aria-hidden', 'true')
    expect(input()).toHaveAttribute('wrap', 'off')
    expect(input().parentElement).toHaveClass('min-h-0', 'flex-1', 'overflow-hidden')
    expect(input().parentElement).not.toHaveClass('min-h-[430px]', 'h-full')
    expect(screen.getByRole('region', { name: 'YAML 编辑器' })).toHaveTextContent('先应用到编辑器，再保存到服务器')
    expect(readDraft(state().sessionId, 'yaml')).toBeUndefined()
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(onImport).not.toHaveBeenCalled()
  })

  it('updates highlighting without rewriting raw draft text, focus, metadata or the live spec', () => {
    const onImport = vi.fn()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    const textarea = input()
    textarea.focus()
    fireEvent.change(textarea, { target: { value: draft } })
    expect(input()).toBe(textarea)
    expect(input()).toHaveFocus()
    expect(input()).toHaveValue(draft)
    expect(highlight().textContent).toBe(draft)
    expect(tokens('string')).toContain('"新的标题"')
    expect(tokens('comment')).toEqual(expect.arrayContaining(['# 原样保留 <img src=x>', '# spacing']))
    expect(highlight().querySelector('img')).toBeNull()
    expect(readDraft(state().sessionId, 'yaml')).toEqual({ text: draft, baseline: stringify(spec, { lineWidth: 0 }), owner: spec.id })
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(state().spec).toEqual(spec)
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(onImport).not.toHaveBeenCalled()
  })

  it('replaces a Tab selection with two spaces and restores the same textarea caret in rAF', () => {
    let frame: FrameRequestCallback | undefined
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frame = callback; return 1 })
    render(<YamlEditor spec={spec} onImport={vi.fn()} />)
    fireEvent.change(input(), { target: { value: draft } })
    const textarea = input()
    const start = draft.indexOf('新的标题')
    const end = start + '新的标题'.length
    textarea.focus()
    textarea.setSelectionRange(start, end)
    const expected = draft.slice(0, start) + '  ' + draft.slice(end)
    expect(fireEvent.keyDown(textarea, { key: 'Tab' })).toBe(false)
    expect(frame).toBeDefined()
    act(() => frame!(0))
    expect(input()).toBe(textarea)
    expect(input()).toHaveFocus()
    expect(input()).toHaveValue(expected)
    expect(textarea.selectionStart).toBe(start + 2)
    expect(textarea.selectionEnd).toBe(start + 2)
    expect(readDraft(state().sessionId, 'yaml')?.text).toBe(expected)
    expect(highlight().textContent).toBe(expected)
    expect(mocks.validate).not.toHaveBeenCalled()
  })

  it.each(['ctrlKey', 'metaKey'] as const)('retains %s+Enter validation and apply semantics', async modifier => {
    const onImport = vi.fn()
    mocks.validate.mockResolvedValue({ valid: true })
    render(<YamlEditor spec={spec} onImport={onImport} />)
    fireEvent.change(input(), { target: { value: draft } })
    expect(fireEvent.keyDown(input(), { key: 'Enter', [modifier]: true })).toBe(false)
    await waitFor(() => expect(onImport).toHaveBeenCalledWith(parseWorkflowYaml(draft)))
    expect(mocks.validate).toHaveBeenCalledOnce()
    expect(mocks.validate).toHaveBeenCalledWith(parseWorkflowYaml(draft))
    expect(state().pendingEdits).not.toHaveProperty('yaml')
    expect(readDraft(state().sessionId, 'yaml')).toBeUndefined()
    expect(highlight().textContent).toBe(input().value)
  })

  it('keeps invalid local YAML highlighted and rejects shortcut apply without a server call', () => {
    const onImport = vi.fn()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    const invalid = '# unfinished\nnodes: [\n'
    fireEvent.change(input(), { target: { value: invalid } })
    fireEvent.keyDown(input(), { key: 'Enter', ctrlKey: true })
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    expect(input()).toHaveValue(invalid)
    expect(highlight().textContent).toBe(invalid)
    expect(tokens('key')).toContain('nodes')
    expect(readDraft(state().sessionId, 'yaml')?.text).toBe(invalid)
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(onImport).not.toHaveBeenCalled()
  })

  it('keeps the highlighted draft and validation issues when shortcut validation fails on the server', async () => {
    const onImport = vi.fn()
    mocks.validate.mockResolvedValue({ valid: false, issues: [{ path: 'title', message: '标题不符合约束' }] })
    render(<YamlEditor spec={spec} onImport={onImport} />)
    fireEvent.change(input(), { target: { value: draft } })
    fireEvent.keyDown(input(), { key: 'Enter', metaKey: true })
    expect(await screen.findByRole('alert')).toHaveTextContent('title：标题不符合约束')
    expect(input()).toHaveValue(draft)
    expect(highlight().textContent).toBe(draft)
    expect(readDraft(state().sessionId, 'yaml')?.text).toBe(draft)
    expect(onImport).not.toHaveBeenCalled()
  })

  it('restores verbatim highlighting after remount without applying or normalizing a draft', () => {
    const onImport = vi.fn()
    const view = render(<YamlEditor spec={spec} onImport={onImport} />)
    fireEvent.change(input(), { target: { value: draft } })
    view.unmount()
    render(<YamlEditor spec={spec} onImport={onImport} />)
    expect(input()).toHaveValue(draft)
    expect(highlight().textContent).toBe(draft)
    expect(tokens('string')).toContain('"新的标题"')
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(onImport).not.toHaveBeenCalled()
  })

  it('keeps the empty-spec editor disabled and cannot create a draft through keyboard input', async () => {
    const onImport = vi.fn()
    render(<YamlEditor spec={null} onImport={onImport} />)
    expect(input()).toBeDisabled()
    await userEvent.setup().type(input(), 'id: unwanted{Tab}{Control>}{Enter}{/Control}')
    expect(input()).toHaveValue('')
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    expect(readDraft(state().sessionId, 'yaml')).toBeUndefined()
    expect(mocks.validate).not.toHaveBeenCalled()
    expect(onImport).not.toHaveBeenCalled()
  })
})
