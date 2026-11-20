import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeExecutionList from '../NodeExecutionList'

vi.mock('../AnalysisModal', () => ({
  default: ({ node, onClose }: { node: NodeExecution; onClose: () => void }) => (
    <div role="dialog" aria-label={`节点分析：${node.node_id}`}>
      <button onClick={onClose}>关闭</button>
    </div>
  ),
}))

const node: NodeExecution = {
  flow_id: 'flow-1',
  node_id: 'node-1',
  node_title: null,
  executor_type: 'action',
  triggered_by: null,
  phase: null,
  branch_id: null,
  session_key: null,
  session_id: 'session-1',
  embedded_session_key: null,
  status: 'succeeded',
  started_at: null,
  completed_at: null,
  duration_ms: null,
  input_json: null,
  output_json: null,
  error_text: null,
  token_usage_json: null,
  system_context_json: null,
  progress_message: null,
  resolved_prompt: null,
  attempt: 1,
}

const listProps = { nodes: [node], selectedNodeId: node.node_id, onSelectNode: vi.fn() }

describe('NodeExecutionList analysis entry', () => {
  it('delegates to onAnalyze once without opening a local modal', () => {
    const onAnalyze = vi.fn()
    render(<NodeExecutionList {...listProps} onAnalyze={onAnalyze} />)

    fireEvent.click(screen.getByRole('button', { name: '节点分析' }))

    expect(onAnalyze).toHaveBeenCalledExactlyOnceWith(node)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens a closable local modal when onAnalyze is omitted', () => {
    render(<NodeExecutionList {...listProps} />)

    fireEvent.click(screen.getByRole('button', { name: '节点分析' }))

    expect(screen.getByRole('dialog', { name: '节点分析：node-1' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
