import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
import RepairItems, { RepairStateCounts } from '../RepairItems'
import RepairItemDetail from '../RepairItemDetail'

const item = (overrides: Partial<RepairInboxItem> = {}): RepairInboxItem => ({
  itemId: 'one', groupKey: 'group', proposalKey: 'proposal', contentRevision: 1, previousItemId: null,
  proposal: { summary: '增加重试次数', operations: [{ nodeId: 'query', path: 'retry.maxAttempts', value: 2 }] },
  instruction: '保留业务逻辑', sources: [], workflowId: 'wf', episodeKey: 'initial', state: 'pending',
  stateVersion: 0, activeTaskId: null, activeRevision: null, disposition: null, updatedAtMs: 1, sourceAvailable: true,
  ...overrides,
})

describe('readable repair candidates', () => {
  it('reports actual mixed states without treating suggestion IDs as run evidence', () => {
    const items = [item(), item({ itemId: 'two', state: 'no_action',
      sources: [{ kind: 'suggestion', suggestionId: 'legacy', proposalDigest: null, instructionDigest: 'digest' }] })]
    render(<><RepairStateCounts items={items} /><RepairItems embedded items={items} selected={[]} onToggle={() => {}} canEdit limit={100} /></>)
    expect(screen.getByText('待处理 1')).toBeVisible()
    expect(screen.getByText('暂不处理 1')).toBeVisible()
    expect(screen.getAllByText(/来源运行数未知/)).toHaveLength(2)
    expect(screen.queryByText(/影响 1 个运行/)).not.toBeInTheDocument()
  })

  it('shows readable diagnostic evidence, keeps raw payload collapsed and tolerates unstructured proposals', async () => {
    const candidate = item({ proposal: { summary: '人工核对', operations: [null, 'text'] }, context: { diagnoses: [{
      nodeId: 'query', reasoning: '上游连接超时', flowId: 'run', analysisId: 'AN',
      evidence: [{ eventId: 'EV', missing: true }],
    }] } })
    render(<RepairItemDetail item={candidate} detail={candidate} selected={false} onToggle={() => {}} canEdit limitReached={false} onLoad={() => {}} onDisposition={() => {}} />)
    expect(screen.getByText(/尚无结构化修改明细/)).toBeVisible()
    await userEvent.click(screen.getByText('query · 来源运行 1'))
    expect(screen.getByText('上游连接超时')).toBeVisible()
    expect(screen.getByText('原始证据已缺失')).toBeVisible()
    expect(screen.getByText('技术详情（来源标识与完整载荷）').closest('details')).not.toHaveAttribute('open')
  })

  it('shows the complete proposed value outside technical data and copies it unchanged', async () => {
    const user = userEvent.setup()
    const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const value = '输入：{{input.params.raw_message}}\n'.repeat(30)
    const candidate = item({ proposal: { summary: '修正输入', operations: [{ path: '/executor/prompt', value }] } })
    render(<RepairItemDetail item={candidate} detail={candidate} selected={false} onToggle={() => {}} canEdit limitReached={false} onLoad={() => {}} onDisposition={() => {}} />)
    await user.click(screen.getByText('展开完整目标值'))
    expect(screen.getByText(value.trim(), { exact: false, selector: 'pre', normalizer: text => text.trim() })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '复制目标值' }))
    expect(copy).toHaveBeenCalledWith(value)
    expect(screen.getByRole('status')).toHaveTextContent('已复制')
    expect(screen.getByText('技术详情（来源标识与完整载荷）').closest('details')).not.toHaveAttribute('open')
  })
})
