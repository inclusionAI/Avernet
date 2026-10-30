import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeExecutionList from '../NodeExecutionList'

vi.mock('../NodeOutputViewer', () => ({ default: () => null }))
vi.mock('../NodeInputPanel', () => ({ default: () => null }))
vi.mock('../RenderedNodeHero', () => ({ default: () => null }))
vi.mock('../NodeStepTracePanel', () => ({ default: () => null }))
vi.mock('../AnalysisModal', () => ({ default: () => null }))

const node = (id: string, executor: string) => ({ flow_id: 'parent', node_id: id, node_title: id, executor_type: executor, status: 'succeeded', attempt: 3 }) as NodeExecution

describe('subworkflow node links', () => {
  it('only places the child links on subworkflow execution rows', () => {
    const renderLinks = vi.fn((item: NodeExecution) => <span>链接 {item.node_id}</span>)
    render(<NodeExecutionList nodes={[node('sub', 'subworkflow'), node('ordinary', 'action')]} onSelectNode={() => {}} onAnalyze={() => {}} renderSubworkflowLinks={renderLinks} />)
    const row = screen.getByText('链接 sub').closest('tr')!
    expect(within(row).getAllByText('sub')).toHaveLength(2)
    expect(screen.queryByText('链接 ordinary')).not.toBeInTheDocument()
    expect(renderLinks).toHaveBeenCalledTimes(1)
  })
})
