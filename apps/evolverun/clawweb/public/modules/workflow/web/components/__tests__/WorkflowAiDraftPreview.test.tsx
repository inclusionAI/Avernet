import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify as stringifyYaml } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { WorkflowAiDraft } from '../../services/workflow-ai-draft'
import WorkflowAiDraftPreview from '../WorkflowAiDraftPreview'

vi.mock('yaml', async (importOriginal) => {
  const actual = await importOriginal<typeof import('yaml')>()
  return { ...actual, stringify: vi.fn(actual.stringify) }
})

const serialize = vi.mocked(stringifyYaml)
const source: WorkflowSpec = {
  id: 'review-flow', version: '1', title: 'Original review', config: { retries: 1 },
  nodes: [{ id: 'review', executor: { type: 'embedded-agent' } }],
}
function makeDraft(spec: WorkflowSpec, metadataChanges: string[]): WorkflowAiDraft {
  return {
    workflowId: source.id, baseSignature: '', summary: '仅调整顶层配置', spec,
    yaml: 'title: Proposed review\n', changes: [], metadataChanges, removedNodeIds: [], warnings: [],
  }
}
function preview(spec: WorkflowSpec, fields: string[], baseSpec = source) {
  const onApply = vi.fn()
  const draft = makeDraft(spec, fields)
  const props = { draft, baseSpec, stale: false, busy: false, onApply }
  return { ...render(<WorkflowAiDraftPreview {...props} />), props, onApply, draft }
}
async function expand(field: string) {
  fireEvent.click(screen.getByText(`查看元数据前后对比 · ${field}`))
  return {
    before: await screen.findByLabelText(`元数据 ${field} 修改前`),
    after: screen.getByLabelText(`元数据 ${field} 修改后`),
  }
}
const apply = () => screen.getByRole('button', { name: '应用草稿到当前 Editor' })

beforeEach(() => { serialize.mockClear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('WorkflowAiDraftPreview metadata comparisons', () => {
  it('lazily serializes each field independently and hides its values again on collapse', async () => {
    preview({ ...source, title: 'Proposed review', config: { retries: 3 } }, ['title', 'config'])
    expect(screen.getByText('顶层元数据变更')).toBeVisible()
    expect(screen.queryByLabelText('元数据 title 修改前')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('元数据 config 修改前')).not.toBeInTheDocument()
    expect(serialize).not.toHaveBeenCalled()

    const config = await expand('config')
    expect(config.before).toBeVisible()
    expect(config.before).toHaveTextContent('retries: 1')
    expect(config.after).toBeVisible()
    expect(config.after).toHaveTextContent('retries: 3')
    expect(screen.queryByLabelText('元数据 title 修改前')).not.toBeInTheDocument()
    expect(serialize.mock.calls.map(([value]) => value)).toEqual([{ retries: 1 }, { retries: 3 }])

    const title = await expand('title')
    expect(title.before).toHaveTextContent('Original review')
    expect(title.after).toHaveTextContent('Proposed review')
    fireEvent.click(screen.getByText('查看元数据前后对比 · config'))
    await waitFor(() => expect(screen.queryByLabelText('元数据 config 修改前')).not.toBeInTheDocument())
    expect(title.before).toBeVisible()
    expect(title.after).toBeVisible()
  })

  it.each([
    ['added', {}, { externalOptions: { labels: ['manual', 'audit'] } }, '（不存在）', 'labels: - manual - audit'],
    ['removed', { externalOptions: { retries: 2 } }, {}, 'retries: 2', '（已删除）'],
    ['null', { externalOptions: null }, { externalOptions: false }, 'null', 'false'],
    ['zero and empty string', { externalOptions: 0 }, { externalOptions: '' }, '0', '""'],
  ])('shows exact before/after states for %s metadata', async (_name, previous, next, beforeText, afterText) => {
    preview({ ...source, ...next }, ['externalOptions'], { ...source, ...previous })
    const values = await expand('externalOptions')
    expect(values.before).toBeVisible()
    expect(values.before).toHaveTextContent(beforeText)
    expect(values.after).toBeVisible()
    expect(values.after).toHaveTextContent(afterText)
  })

  it('keeps metadata-only application blocked until explicit confirmation, including after review', async () => {
    const { onApply, draft } = preview({ ...source, config: { retries: 3 } }, ['config'])
    expect(apply()).toBeDisabled()
    fireEvent.click(apply())
    await expand('config')
    expect(apply()).toBeDisabled()
    expect(onApply).not.toHaveBeenCalled()
    const confirmation = screen.getByRole('checkbox', { name: '我已审阅并确认删除节点或修改顶层元数据' })
    fireEvent.click(confirmation)
    expect(apply()).toBeEnabled()
    fireEvent.click(confirmation)
    expect(apply()).toBeDisabled()
    fireEvent.click(confirmation)
    fireEvent.click(apply())
    expect(onApply).toHaveBeenCalledExactlyOnceWith(draft)
  })

  it('preserves expanded comparisons and confirmation across a temporary block', async () => {
    const { rerender, props, onApply } = preview({ ...source, config: { retries: 3 } }, ['config'])
    await expand('config')
    fireEvent.click(screen.getByRole('checkbox'))
    rerender(<WorkflowAiDraftPreview {...props} blockedReason="请先重置未应用的 YAML。" />)
    expect(screen.getByLabelText('元数据 config 修改前')).toBeVisible()
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(apply()).toBeDisabled()
    fireEvent.click(apply())
    expect(onApply).not.toHaveBeenCalled()
    rerender(<WorkflowAiDraftPreview {...props} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(apply()).toBeEnabled()
  })

  it.each(['stale', 'busy'] as const)('blocks a confirmed metadata draft while %s', async (state) => {
    const { rerender, props, onApply } = preview({ ...source, config: { retries: 3 } }, ['config'])
    fireEvent.click(screen.getByRole('checkbox'))
    expect(apply()).toBeEnabled()
    rerender(<WorkflowAiDraftPreview {...props} {...{ [state]: true }} />)
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(apply()).toBeDisabled()
    fireEvent.click(apply())
    expect(onApply).not.toHaveBeenCalled()
  })
})
