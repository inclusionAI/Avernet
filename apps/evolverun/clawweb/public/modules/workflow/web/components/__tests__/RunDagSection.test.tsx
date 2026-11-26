import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunDagViewProps } from '../RunDagView'

const graph = vi.hoisted(() => ({ render: vi.fn() }))
vi.mock('../RunDagView', () => ({
  default: (props: RunDagViewProps) => {
    graph.render(props)
    return (
      <div data-testid="dag-view" data-expanded={String(props.expanded)}>
        {props.specNodes.map(node => (
          <button type="button" key={node.id} onClick={() => props.onNodeClick(node.id)}>{node.id}</button>
        ))}
      </div>
    )
  },
}))

import RunDagSection from '../RunDagSection'

const specNodes: WorkflowNode[] = [
  { id: 'historical-a', title: '登记时的节点 A', executor: { type: 'action' } },
  { id: 'historical-b', title: '登记时的节点 B', dependsOn: ['historical-a'], executor: { type: 'bot' } },
]
const defaultProps = { specNodes, nodeStatusMap: {}, onNodeClick: vi.fn() }

beforeEach(() => { graph.render.mockClear(); defaultProps.onNodeClick.mockClear() })
afterEach(cleanup)

describe('RunDagSection', () => {
  it('provides an independently named card and counts spec nodes, not execution records', () => {
    render(<RunDagSection {...defaultProps} nodeStatusMap={{ 'dynamic-only': { status: 'succeeded' } }} />)
    expect(screen.getByRole('region', { name: '运行 DAG' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: '运行 DAG' })).toBeVisible()
    expect(screen.getByText('2 个节点')).toBeVisible()
    expect(screen.getByLabelText('节点状态图例')).toBeVisible()
    expect(screen.getByText(/无执行记录的节点保留定义，不推断执行结果/)).toBeVisible()
    expect(graph.render).toHaveBeenLastCalledWith(expect.objectContaining({ specNodes }))
  })

  it.each([false, true])('shows loading for undefined specNodes (readOnly=%s)', readOnly => {
    render(<RunDagSection specNodes={undefined} nodeStatusMap={{}} onNodeClick={vi.fn()} readOnly={readOnly} />)
    expect(screen.getByRole('status')).toHaveTextContent(readOnly ? '正在加载本次运行的静态 DAG 快照' : '正在加载工作流节点定义')
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.queryByText('0 个节点')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeDisabled()
    expect(graph.render).not.toHaveBeenCalled()
  })

  it('fails closed on snapshot errors even when stale spec nodes are still supplied', () => {
    render(<RunDagSection {...defaultProps} readOnly snapshotError selectedNodeId="historical-a" />)
    expect(screen.getByRole('alert')).toHaveTextContent('无法确认本次运行的历史 DAG')
    expect(screen.getByRole('alert')).toHaveTextContent('不会使用当前工作流定义替代')
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.queryByText('2 个节点')).not.toBeInTheDocument()
    expect(screen.queryByText(/已选择/)).not.toBeInTheDocument()
    expect(graph.render).not.toHaveBeenCalled()
  })

  it('renders only supplied historical topology and removes it on a later authorization error', () => {
    const { rerender } = render(<RunDagSection {...defaultProps} specNodes={undefined} readOnly />)
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    rerender(<RunDagSection {...defaultProps} readOnly />)
    expect(screen.getByTestId('dag-view')).toBeVisible()
    expect(screen.getByText('historical-a')).toBeVisible()
    expect(screen.getByText(/静态 DAG.*不含动态节点.*尚未验证/)).toBeVisible()
    rerender(<RunDagSection {...defaultProps} readOnly snapshotError />)
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeVisible()
    rerender(<RunDagSection {...defaultProps} readOnly specNodes={undefined} />)
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载本次运行的静态 DAG 快照')
  })

  it('treats an empty historical topology as loaded, not missing', () => {
    render(<RunDagSection {...defaultProps} specNodes={[]} readOnly />)
    expect(screen.getByText('0 个节点')).toBeVisible()
    expect(screen.getByTestId('dag-view')).toBeVisible()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(graph.render).toHaveBeenLastCalledWith(expect.objectContaining({ specNodes: [] }))
    expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeDisabled()
  })

  it('does not apply the historical error flag to a live main run', () => {
    render(<RunDagSection {...defaultProps} snapshotError />)
    expect(screen.getByTestId('dag-view')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/历史快照 · 只读/)).not.toBeInTheDocument()
  })

  it('shows a live definition error instead of indefinite loading without a retry handler', () => {
    render(<RunDagSection {...defaultProps} specNodes={undefined} definitionError />)
    expect(screen.getByRole('alert')).toHaveTextContent('工作流节点定义加载失败，暂无法展示 DAG。')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '重新加载节点定义' })).not.toBeInTheDocument()
    expect(graph.render).not.toHaveBeenCalled()
  })

  it('hides cached live nodes and selection on definition error and retries only on request', async () => {
    const user = userEvent.setup()
    const onRetryDefinition = vi.fn()
    const { rerender } = render(<RunDagSection {...defaultProps} selectedNodeId="historical-a" onRetryDefinition={onRetryDefinition} />)
    expect(screen.getByTestId('dag-view')).toBeVisible()
    graph.render.mockClear()
    rerender(<RunDagSection {...defaultProps} selectedNodeId="historical-a" definitionError onRetryDefinition={onRetryDefinition} />)
    expect(screen.getByRole('alert')).toHaveTextContent('工作流节点定义加载失败')
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    expect(screen.queryByText('2 个节点')).not.toBeInTheDocument()
    expect(screen.queryByText(/已选择/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeDisabled()
    expect(graph.render).not.toHaveBeenCalled()
    expect(onRetryDefinition).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '重新加载节点定义', exact: true }))
    expect(onRetryDefinition).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
    rerender(<RunDagSection {...defaultProps} specNodes={undefined} onRetryDefinition={onRetryDefinition} />)
    expect(screen.getByRole('status')).toHaveTextContent('正在加载工作流节点定义')
    expect(screen.queryByRole('button', { name: '重新加载节点定义' })).not.toBeInTheDocument()
    rerender(<RunDagSection {...defaultProps} onRetryDefinition={onRetryDefinition} />)
    expect(screen.getByTestId('dag-view')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeEnabled()
  })

  it.each(['loaded', 'loading', 'error'])('ignores live definition error/retry in a %s historical view', state => {
    const onRetryDefinition = vi.fn()
    render(<RunDagSection {...defaultProps} readOnly definitionError onRetryDefinition={onRetryDefinition}
      specNodes={state === 'loading' ? undefined : specNodes} snapshotError={state === 'error'} />)
    expect(screen.queryByText(/工作流节点定义加载失败/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新加载节点定义' })).not.toBeInTheDocument()
    expect(onRetryDefinition).not.toHaveBeenCalled()
    if (state === 'loaded') {
      expect(screen.getByTestId('dag-view')).toBeVisible()
      expect(screen.getByText('2 个节点')).toBeVisible()
      expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeEnabled()
    } else {
      expect(screen.queryByTestId('dag-view')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: '展开 DAG 画布' })).toBeDisabled()
      if (state === 'loading') expect(screen.getByRole('status')).toHaveTextContent('正在加载本次运行的静态 DAG 快照')
      else expect(screen.getByRole('alert')).toHaveTextContent('无法确认本次运行的历史 DAG')
    }
  })

  it('passes selected ID, status evidence, and the exact callback to the graph', async () => {
    const user = userEvent.setup()
    const nodeStatusMap = { 'historical-a': { status: 'running', executorType: 'bot', progressMessage: '检查中' } }
    render(<RunDagSection {...defaultProps} nodeStatusMap={nodeStatusMap} selectedNodeId="historical-a" />)
    expect(graph.render).toHaveBeenLastCalledWith(expect.objectContaining({
      nodeStatusMap, selectedNodeId: 'historical-a', onNodeClick: defaultProps.onNodeClick,
    }))
    expect(screen.getByText('已选择：登记时的节点 A')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'historical-b' }))
    expect(defaultProps.onNodeClick.mock.calls).toEqual([['historical-b']])
  })

  it('expands/restores the canvas without remounting or losing selection', async () => {
    const user = userEvent.setup()
    render(<RunDagSection {...defaultProps} selectedNodeId="historical-b" />)
    const originalCanvas = screen.getByTestId('dag-view')
    const expand = screen.getByRole('button', { name: '展开 DAG 画布' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(expand.getAttribute('aria-controls')!)).toContainElement(originalCanvas)
    await user.click(expand)
    expect(screen.getByRole('button', { name: '收起 DAG 画布' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('dag-view')).toBe(originalCanvas)
    expect(originalCanvas).toHaveAttribute('data-expanded', 'true')
    expect(screen.getByText('已选择：登记时的节点 B')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '收起 DAG 画布' }))
    expect(originalCanvas).toHaveAttribute('data-expanded', 'false')
    expect(defaultProps.onNodeClick).not.toHaveBeenCalled()
  })
})
