import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify as stringifyYaml } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { generateWorkflowAiDraft, workflowSpecSignature, type WorkflowAiDraft } from '../../services/workflow-ai-draft'
import WorkflowAiDrawer from '../WorkflowAiDrawer'

vi.mock('../../services/workflow-ai-draft', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/workflow-ai-draft')>()
  return { ...actual, generateWorkflowAiDraft: vi.fn() }
})

const generate = vi.mocked(generateWorkflowAiDraft)
const source: WorkflowSpec = {
  id: 'review-flow', version: '1', title: 'Review',
  nodes: [{ id: 'review', title: 'Review', executor: { type: 'embedded-agent', prompt: 'Before review' } }],
}
function makeDraft(overrides: Partial<WorkflowAiDraft> = {}): WorkflowAiDraft {
  const spec: WorkflowSpec = {
    ...source,
    nodes: [{ ...source.nodes[0], executor: { type: 'embedded-agent', prompt: 'After review' } }],
  }
  return {
    workflowId: source.id, baseSignature: workflowSpecSignature(source), summary: '已调整复核提示词', spec,
    yaml: stringifyYaml(spec), changes: [{ kind: 'update', nodeId: 'review', title: 'Review', reason: '按要求细化复核' }],
    warnings: ['检查模型输出', '保存前复核参数'], metadataChanges: [], removedNodeIds: [], ...overrides,
  }
}
const props = () => ({ open: true, mode: 'modify' as const, spec: source, onClose: vi.fn(), onApply: vi.fn() })
const prompt = () => screen.getByRole('textbox')
const submit = () => screen.getByRole('button', { name: '生成 YAML 草稿' })
async function generatePreview() {
  fireEvent.change(prompt(), { target: { value: '调整复核提示词' } })
  fireEvent.click(submit())
  await screen.findByRole('region', { name: 'AI 草稿预览' })
}

beforeEach(() => { generate.mockReset(); generate.mockResolvedValue(makeDraft()) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('WorkflowAiDrawer', () => {
  it.each(['create', 'modify'] as const)('discloses service data sharing and manual save/deploy before generating in %s mode', (mode) => {
    const p = props()
    render(<WorkflowAiDrawer {...p} mode={mode} />)
    const disclosure = screen.getByText('生成时会将当前工作流的完整 YAML 和需求描述发送到已配置的智能接入生成服务。生成和应用草稿均不会自动保存或部署，需另行操作。')
    expect(disclosure).toBeVisible()
    expect(disclosure.compareDocumentPosition(submit()) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    expect(submit()).toHaveAccessibleDescription(disclosure.textContent!)
    expect(prompt()).toHaveAccessibleDescription(expect.stringContaining(disclosure.textContent!))
    expect(generate).not.toHaveBeenCalled()
    expect(p.onApply).not.toHaveBeenCalled()
  })

  it.each(['请先应用或重置未应用的 YAML。', '正在保存，请等待保存完成。'])('blocks generation with a clear reason: %s', async (blockedReason) => {
    const p = props()
    const view = render(<WorkflowAiDrawer {...p} blockedReason={blockedReason} />)
    expect(screen.getByRole('status')).toHaveTextContent(blockedReason)
    fireEvent.change(prompt(), { target: { value: '调整复核提示词' } })
    expect(submit()).toBeDisabled()
    fireEvent.click(submit())
    fireEvent.submit(submit().closest('form')!)
    expect(generate).not.toHaveBeenCalled()
    expect(p.onApply).not.toHaveBeenCalled()
    view.rerender(<WorkflowAiDrawer {...p} />)
    expect(screen.queryByText(blockedReason)).not.toBeInTheDocument()
    expect(prompt()).toHaveValue('调整复核提示词')
    fireEvent.click(submit())
    await screen.findByRole('region', { name: 'AI 草稿预览' })
    expect(generate).toHaveBeenCalledTimes(1)
  })

  it('does not cancel on blocking alone and retains the completed preview until unblocked', async () => {
    let resolve!: (draft: WorkflowAiDraft) => void
    generate.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const p = props()
    const view = render(<WorkflowAiDrawer {...p} />)
    fireEvent.change(prompt(), { target: { value: '调整复核提示词' } })
    fireEvent.click(submit())
    const signal = generate.mock.calls[0][3]!
    view.rerender(<WorkflowAiDrawer {...p} blockedReason="正在保存，请等待保存完成。" />)
    expect(signal.aborted).toBe(false)
    await act(async () => { resolve(makeDraft()) })
    const preview = screen.getByRole('region', { name: 'AI 草稿预览' })
    const apply = screen.getByRole('button', { name: '应用草稿到当前 Editor' })
    expect(preview).toHaveTextContent('已调整复核提示词')
    expect(apply).toBeDisabled()
    fireEvent.click(apply)
    fireEvent.submit(submit().closest('form')!)
    expect(p.onApply).not.toHaveBeenCalled()
    expect(generate).toHaveBeenCalledTimes(1)
    view.rerender(<WorkflowAiDrawer {...p} />)
    expect(screen.getByRole('region', { name: 'AI 草稿预览' })).toBe(preview)
    expect(apply).toBeEnabled()
    fireEvent.click(apply)
    expect(p.onApply).toHaveBeenCalledExactlyOnceWith(makeDraft())
  })

  it('generates real-service output, previews changes and only applies on explicit action', async () => {
    const p = props()
    render(<WorkflowAiDrawer {...p} />)
    expect(screen.getByRole('region', { name: 'AI 工作流助手' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('沿用智能接入生成服务')).toBeInTheDocument()
    expect(prompt()).toHaveFocus()
    await generatePreview()
    expect(generate).toHaveBeenCalledWith('modify', '调整复核提示词', source, expect.any(AbortSignal))
    expect(p.onApply).not.toHaveBeenCalled()
    expect(screen.getByText('已调整复核提示词')).toBeInTheDocument()
    expect(screen.getByText('按要求细化复核')).toBeInTheDocument()
    expect(screen.getByText('检查模型输出')).toBeInTheDocument()
    expect(screen.getByText('保存前复核参数')).toBeInTheDocument()
    fireEvent.click(screen.getByText('查看节点前后对比 · review'))
    expect(await screen.findByLabelText('review 修改前')).toHaveTextContent('Before review')
    expect(screen.getByLabelText('review 修改后')).toHaveTextContent('After review')
    fireEvent.click(screen.getByText('查看完整 YAML 草稿'))
    expect(screen.getByLabelText('生成的 YAML')).toHaveTextContent('After review')
    fireEvent.click(screen.getByRole('button', { name: '应用草稿到当前 Editor' }))
    expect(p.onApply).toHaveBeenCalledExactlyOnceWith(makeDraft())
    expect(p.onClose).not.toHaveBeenCalled()
  })

  it('does not generate for blank input or an unloaded workflow', () => {
    const p = props()
    const view = render(<WorkflowAiDrawer {...p} />)
    expect(submit()).toBeDisabled()
    fireEvent.change(prompt(), { target: { value: '   ' } })
    fireEvent.click(submit())
    expect(generate).not.toHaveBeenCalled()
    view.rerender(<WorkflowAiDrawer {...p} spec={null} />)
    fireEvent.change(prompt(), { target: { value: '添加检查' } })
    expect(submit()).toBeDisabled()
  })

  it('reports errors inline and supports retry without applying a failed response', async () => {
    generate.mockRejectedValueOnce(new Error('生成服务暂时不可用'))
    const p = props()
    render(<WorkflowAiDrawer {...p} />)
    fireEvent.change(prompt(), { target: { value: '调整复核' } })
    fireEvent.click(submit())
    expect(await screen.findByRole('alert')).toHaveTextContent('生成服务暂时不可用')
    expect(screen.getByRole('alert').parentElement).toHaveAttribute('aria-live', 'polite')
    expect(screen.queryByRole('button', { name: '应用草稿到当前 Editor' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试生成' }))
    await screen.findByText('已调整复核提示词')
    expect(generate).toHaveBeenCalledTimes(2)
    expect(p.onApply).not.toHaveBeenCalled()
  })

  it('invalidates previews on prompt or mode change; mode controls are ordinary keyboard buttons', async () => {
    render(<WorkflowAiDrawer {...props()} />)
    await generatePreview()
    fireEvent.change(prompt(), { target: { value: '改成其他需求' } })
    expect(screen.queryByText('已调整复核提示词')).not.toBeInTheDocument()
    fireEvent.click(submit())
    await screen.findByText('已调整复核提示词')
    const add = screen.getByRole('button', { name: '增加能力' })
    add.focus()
    await userEvent.keyboard('{Enter}')
    expect(add).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByText('已调整复核提示词')).not.toBeInTheDocument()
    expect(screen.getByLabelText('描述想增加的能力')).toHaveValue('改成其他需求')
  })

  it('examples only populate the prompt and never generate or apply', () => {
    const p = props()
    render(<WorkflowAiDrawer {...p} />)
    fireEvent.click(screen.getByRole('button', { name: '调整现有人工确认节点的超时配置，并说明影响' }))
    expect(prompt()).toHaveValue('调整现有人工确认节点的超时配置，并说明影响')
    expect(generate).not.toHaveBeenCalled()
    expect(p.onApply).not.toHaveBeenCalled()
  })

  it('marks completed drafts stale on spec edits and allows regeneration against the new spec', async () => {
    const p = props()
    const view = render(<WorkflowAiDrawer {...p} />)
    await generatePreview()
    const edited = { ...source, title: 'Edited locally' }
    view.rerender(<WorkflowAiDrawer {...p} spec={edited} />)
    expect(screen.getByRole('status')).toHaveTextContent('草稿已过期')
    expect(screen.getByRole('button', { name: '应用草稿到当前 Editor' })).toBeDisabled()
    generate.mockResolvedValueOnce(makeDraft({ baseSignature: workflowSpecSignature(edited) }))
    fireEvent.click(screen.getByRole('button', { name: '重新生成草稿' }))
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '应用草稿到当前 Editor' })).toBeEnabled()
    expect(generate).toHaveBeenLastCalledWith('modify', '调整复核提示词', edited, expect.any(AbortSignal))
  })

  it.each(['removal', 'metadata'] as const)('requires explicit confirmation for %s changes', async (kind) => {
    generate.mockResolvedValueOnce(kind === 'removal'
      ? makeDraft({ spec: { ...source, nodes: [] }, changes: [{ kind: 'remove', nodeId: 'review', title: 'Review', reason: '移除旧复核' }], removedNodeIds: ['review'] })
      : makeDraft({ metadataChanges: ['config'] }))
    const p = props()
    render(<WorkflowAiDrawer {...p} />)
    await generatePreview()
    const apply = screen.getByRole('button', { name: '应用草稿到当前 Editor' })
    expect(apply).toBeDisabled()
    fireEvent.click(apply)
    expect(p.onApply).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: '我已审阅并确认删除节点或修改顶层元数据' }))
    fireEvent.click(apply)
    expect(p.onApply).toHaveBeenCalledTimes(1)
  })

  it('resets destructive confirmation for a new draft', async () => {
    generate.mockResolvedValue(makeDraft({ metadataChanges: ['config'] }))
    render(<WorkflowAiDrawer {...props()} />)
    await generatePreview()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: '应用草稿到当前 Editor' })).toBeEnabled()
    fireEvent.click(submit())
    await screen.findByRole('checkbox')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '应用草稿到当前 Editor' })).toBeDisabled()
  })

  it('supports Escape without trapping focus and restores the opener focus', async () => {
    const p = props()
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const view = render(<WorkflowAiDrawer {...p} />)
    expect(prompt()).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(p.onClose).toHaveBeenCalledTimes(1)
    expect(opener).toHaveFocus()
    view.rerender(<WorkflowAiDrawer {...p} open={false} />)
    expect(opener).toHaveFocus()
    opener.remove()
  })

  it('cancels generation from the close control and never displays a late result', async () => {
    let resolve!: (draft: WorkflowAiDraft) => void
    generate.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const p = props()
    render(<WorkflowAiDrawer {...p} />)
    fireEvent.change(prompt(), { target: { value: '修改' } })
    fireEvent.click(submit())
    expect(screen.getByRole('button', { name: '生成中…' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '关闭 AI 助手' }))
    expect(generate.mock.calls[0][3]?.aborted).toBe(true)
    await act(async () => { resolve(makeDraft()) })
    expect(screen.queryByText('已调整复核提示词')).not.toBeInTheDocument()
    expect(p.onApply).not.toHaveBeenCalled()
  })
})
