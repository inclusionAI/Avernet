import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeExecutionList from '../NodeExecutionList'
import { trainWatch } from '../../api/__tests__/aistudio-train.fixture'

vi.mock('../RenderedNodeHero', () => ({ default: () => <div>原脚本 Hero</div> }))
vi.mock('../NodeInputPanel', () => ({ default: () => <div>节点输入</div> }))
vi.mock('../NodeOutputViewer', () => ({ default: () => <div>节点输出</div> }))
vi.mock('../NodeStepTracePanel', () => ({ default: () => <div>执行步骤</div> }))
vi.mock('../AnalysisModal', () => ({ default: () => null }))
const node: NodeExecution = {
  flow_id: 'flow-1', node_id: 'train', node_title: '训练', executor_type: 'aistudio-train',
  attempt: 2, status: 'waiting', triggered_by: 'operator', phase: null, branch_id: null,
  session_key: null, session_id: null, embedded_session_key: null, started_at: null,
  completed_at: null, duration_ms: null, input_json: '{}', output_json: '{}', error_text: null,
  token_usage_json: null, system_context_json: '{"phase":"train"}', progress_message: null, resolved_prompt: null,
}
let client: QueryClient
const fetchMock = vi.fn<typeof fetch>()
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  fetchMock.mockReset().mockResolvedValue(new Response(JSON.stringify({ watch: trainWatch })))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals() })
function show(value = node, selectedNodeId: string | undefined = 'train', readOnly = false) {
  const select = vi.fn()
  render(<QueryClientProvider client={client}><NodeExecutionList nodes={[value]} onSelectNode={select} selectedNodeId={selectedNodeId} readOnly={readOnly} onAnalyze={vi.fn()} /></QueryClientProvider>)
  return select
}
describe('AIStudio training inline run integration', () => {
  it('replaces only the selected training hero inside the existing nine-column detail', async () => {
    show()
    expect(await screen.findByRole('region', { name: 'AIStudio 训练观测' })).toBeInTheDocument()
    expect(screen.queryByText('原脚本 Hero')).not.toBeInTheDocument()
    expect(screen.getByText('节点输入')).toBeInTheDocument()
    expect(screen.getByText('节点输出')).toBeInTheDocument()
    expect(screen.getByText('系统上下文')).toBeInTheDocument()
    expect(screen.getByText('运行元数据')).toBeInTheDocument()
    expect(screen.getByText('执行步骤')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'AIStudio 训练观测' }).closest('td')).toHaveAttribute('colspan', '9')
    const card = screen.getByRole('region', { name: 'AIStudio 训练观测' })
    const copy = await within(card).findByRole('button', { name: '复制 recordId' })
    expect(copy.nextElementSibling).toBe(within(card).getByRole('link', { name: '打开 AIStudio 任务' }))
    expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('keeps the compact task link from selecting the node without exposing manual controls', async () => {
    const select = show(node, '')
    const link = await screen.findByRole('link', { name: '打开 AIStudio 任务' })
    fireEvent.click(link)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(select).not.toHaveBeenCalled()
  })
  it('preserves read-only child-run observation without exposing manual operations', async () => {
    show(node, 'train', true)
    expect(await screen.findByRole('region', { name: 'AIStudio 训练观测' })).toBeInTheDocument()
    expect(await screen.findAllByRole('link', { name: '打开 AIStudio 任务' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })
  it('does not request AIStudio watches or replace the hero for other executors', () => {
    show({ ...node, executor_type: 'cli-script' })
    expect(screen.getByText('原脚本 Hero')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'AIStudio 训练观测' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
