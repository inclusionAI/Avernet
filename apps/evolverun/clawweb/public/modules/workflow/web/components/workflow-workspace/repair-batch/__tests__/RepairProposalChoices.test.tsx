import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { proposalChoices } from '../RepairProposalChoices'
import RepairProposalChoices from '../RepairProposalChoices'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'

it('compares actual field values and preserves separate identities instead of merging matching titles', () => {
  const item = (id: string, title: string, value: string) => ({ itemId: id, proposal: { summary: title,
    operations: [{ op: 'replace', nodeId: 'n', path: '/executor/prompt', value }] } }) as RepairInboxItem
  const items = [item('a', '精简 Prompt', 'short'), item('b', '精简 Prompt', 'different'), item('c', '另一标题', 'short')]
  const groups = proposalChoices(items)
  expect(groups).toHaveLength(1)
  expect(groups[0].choices).toHaveLength(2)
  expect(groups[0].choices[0].items.map(item => item.itemId)).toEqual(['a','c'])
  render(<RepairProposalChoices items={items} total={33} />)
  expect(screen.getByText(/同字段有 2 种实际修改/)).toBeVisible()
  expect(screen.getByText(/本页 3.*共 33/)).toBeVisible()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
})
