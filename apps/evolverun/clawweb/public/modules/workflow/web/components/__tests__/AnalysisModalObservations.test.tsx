import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LangfuseObservation, LangfuseTrace, NodeExecution } from '@avernet/clawweb-shared/web/types'
import AnalysisModal from '../AnalysisModal'

const { useLangfuseTraces, mutate } = vi.hoisted(() => ({ useLangfuseTraces: vi.fn(), mutate: vi.fn() }))
vi.mock('@avernet/workflow/web/api/hooks', () => ({
  useLangfuseTraces,
  useAnalyzeTrace: () => ({ mutate, data: { summary: '示例分析摘要', analysis: '', suggestions: [] } }),
}))

const node: NodeExecution = {
  flow_id: 'flow-1', node_id: 'risk-check', node_title: '风险筛查', executor_type: 'agent',
  triggered_by: null, phase: null, branch_id: null, session_key: null, session_id: 'legacy',
  embedded_session_key: 'trace-session', status: 'succeeded', started_at: null, completed_at: null,
  duration_ms: null, input_json: null, output_json: null, error_text: null, token_usage_json: null,
  system_context_json: null, progress_message: null, resolved_prompt: null, attempt: 1,
}
const startTime = '2026-11-18T03:17:19.000Z'
const longInput = `第一段：关联账户\n\n${'long-'.repeat(2400)}\n末尾完整可见`
const textOutput = '风险记录：关联账户\n\n保留原换行和 <b>字面文本</b>。'

function observation(id: string, name: string, input: unknown, output: unknown): LangfuseObservation {
  return {
    id, type: 'span', name, startTime, endTime: null, latencyMs: 38200, input, output, model: null,
    promptTokens: null, completionTokens: null, totalTokens: null, statusMessage: null,
  }
}
function trace(id: string, name: string, observations: LangfuseObservation[], time: string): LangfuseTrace {
  return {
    id, name, observations, startTime: time, endTime: null, latencyMs: 324800, sessionId: 'trace-session',
    input: null, output: null, metadata: null, scores: [],
  }
}
const traces = {
  data: [
    trace('turn-2', 'Turn 2', [
      observation('exec', 'tool exec', longInput, JSON.stringify(textOutput)),
      observation('read', 'tool read', { source: 'demo/risk-guide.md' }, '另一处可直接阅读的输出'),
    ], startTime),
    trace('turn-1', 'Turn 1', [observation('chat', 'chat', '上一轮独有内容', null)], '2026-11-18T03:12:16.000Z'),
  ],
  meta: { page: 1, limit: 25, totalItems: 2 },
}
const openModal = () => render(<AnalysisModal node={node} onClose={vi.fn()} />)
const firstCall = () => screen.getByRole('article', { name: /01.*tool exec/ })
const secondCall = () => screen.getByRole('article', { name: /02.*tool read/ })

describe('node analysis observation reader', () => {
  beforeEach(() => {
    mutate.mockReset()
    mutate.mockImplementation((_request, callbacks) => callbacks.onSuccess())
    useLangfuseTraces.mockReset()
    useLangfuseTraces.mockReturnValue({ status: 'success', data: traces, isLoading: false })
  })

  it('opens with every plain-text input and output intact, without altering the analysis request', () => {
    const { container } = openModal()
    expect(firstCall()).toBeVisible()
    expect(secondCall()).toBeVisible()
    const rendered = Array.from(firstCall().querySelectorAll('pre')).map((el) => el.textContent)
    expect(rendered).toContain(longInput)
    expect(rendered).toContain(textOutput)
    expect(firstCall().querySelector('b')).toBeNull()
    expect(within(secondCall()).getByText('另一处可直接阅读的输出')).toBeVisible()
    expect(container.textContent).not.toContain('... (truncated)')
    expect(mutate).toHaveBeenCalledTimes(1)
    expect(mutate.mock.calls[0][0].traceData).toBe(traces)
  })

  it('finds text after the old cutoff and in collapsed JSON while keeping search scoped to the selected Turn', async () => {
    const user = userEvent.setup()
    openModal()
    const search = screen.getByRole('searchbox', { name: /搜索 Observations/ })
    const json = within(secondCall()).getByText('JSON 正文', { selector: 'summary' }).closest('details')!
    expect(json.open).toBe(false)
    await user.type(search, '末尾完整可见')
    expect(screen.getByText('末尾完整可见', { selector: 'mark' })).toBeInTheDocument()
    await user.clear(search)
    await user.type(search, 'demo/risk-guide.md')
    expect(json.open).toBe(false)
    await user.keyboard('{Enter}')
    expect(json.open).toBe(true)
    expect(within(secondCall()).getByText('demo/risk-guide.md', { selector: 'mark' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Turn 1/ }))
    expect(screen.getByText('上一轮独有内容')).toBeVisible()
    expect(screen.queryByText('demo/risk-guide.md', { selector: 'mark' })).not.toBeInTheDocument()
    expect(screen.getByText('无匹配')).toBeVisible()
  })

  it('limits highlight nodes without losing counts or navigation to later matches', async () => {
    const user = userEvent.setup()
    const { container } = openModal()
    const search = screen.getByRole('searchbox', { name: /搜索 Observations/ })
    await user.type(search, 'long-')
    expect(screen.getByText('2400 处匹配')).toBeVisible()
    expect(container.querySelectorAll('mark').length).toBeLessThan(500)
    await user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(screen.getByText('2400 / 2400')).toBeVisible()
    expect(container.querySelector('mark[data-trace-match="2399"]')).toHaveTextContent('long-')
    expect(Array.from(firstCall().querySelectorAll('pre')).some((el) => el.textContent === longInput)).toBe(true)
  })

  it.each(['observations', 'scores'] as const)('keeps the dialog usable when a legacy trace omits %s', (field) => {
    const { [field]: _missing, ...legacy } = traces.data[0]
    const legacyFeed = { ...traces, data: [legacy] }
    useLangfuseTraces.mockReturnValue({ status: 'success', data: legacyFeed, isLoading: false })
    openModal()
    expect(screen.getByText('示例分析摘要')).toBeVisible()
    expect(mutate.mock.calls[0][0].traceData).toBe(legacyFeed)
    if (field === 'observations') expect(screen.getByText('暂无 Observations')).toBeVisible()
    else expect(firstCall()).toBeVisible()
  })

  it('copies readable text and preserves the exact original encoding as a separate action', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    openModal()
    const call = within(firstCall())
    await user.click(call.getByRole('button', { name: '复制 Output', exact: true }))
    expect(writeText).toHaveBeenLastCalledWith(textOutput)
    await user.click(call.getByText('原始编码', { selector: 'summary' }))
    await user.click(call.getByRole('button', { name: '复制 原始 Output', exact: true }))
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify(textOutput))
  })

  it('jumps to a call, copies complete text, and hides/restores the AI result without changing Turn', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    openModal()
    const jump = screen.getByRole('combobox', { name: /选择要跳转的 Observation/ })
    await user.selectOptions(jump, within(jump).getByRole('option', { name: /02.*tool read/ }))
    await user.click(screen.getByRole('button', { name: '跳到', exact: true }))
    expect(secondCall()).toHaveFocus()
    await user.click(within(firstCall()).getByRole('button', { name: '复制 Input' }))
    expect(writeText).toHaveBeenCalledWith(longInput)
    expect(screen.getByText('示例分析摘要')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '专注阅读' }))
    expect(screen.queryByText('示例分析摘要')).not.toBeVisible()
    expect(firstCall()).toBeVisible()
    await user.click(screen.getByRole('button', { name: '显示 AI 分析' }))
    expect(screen.getByText('示例分析摘要')).toBeVisible()
  })
})
