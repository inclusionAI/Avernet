import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import RepairProposalChoices from '../RepairProposalChoices'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
const item = (id: string, operations: unknown[]) => ({ itemId: id, proposal: { summary: `方案${id}`, operations } }) as RepairInboxItem
const op = (path: string, value: unknown, extra = {}) => ({ nodeId: 'query', op: 'replace', path, value, ...extra })
it('renders nothing for independent fields, different nodes or equal values', () => {
  const a = item('a', [op('/timeout', 600)])
  for (const items of [[a, item('b', [op('/retry', 2)])], [a, item('same', [op('/timeout', 600)])],
    [a, item('other', [op('/timeout', 90, { nodeId: 'other' })])]]) {
    const { container, unmount } = render(<RepairProposalChoices items={items} />)
    expect(container).toBeEmptyDOMElement(); unmount()
  }
})
it('names selected conflicts, target and values without extra selection or JSON directory', () => {
  const remove = vi.fn()
  const items = [item('a', [op('/timeout', 600)]), item('b', [op('/timeout', 90)])]
  render(<RepairProposalChoices items={items} onRemove={remove} />)
  expect(screen.getByRole('alert')).toHaveTextContent('方案a')
  expect(screen.getByRole('alert')).toHaveTextContent('方案b')
  expect(screen.getByText('query · /timeout')).toBeVisible()
  expect(screen.getByText('/timeout → 600')).toBeVisible(); expect(screen.getByText('/timeout → 90')).toBeVisible()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(screen.queryByText(/按实际字段|按原顺序|跨页方案|已验证方案/)).not.toBeInTheDocument()
  expect(screen.getAllByText('完整修改').every(el => !el.closest('details')!.open)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '取消选择 方案b' }))
  expect(remove).toHaveBeenCalledWith('b')
  expect(items.map(i => i.itemId)).toEqual(['a', 'b'])
})
it('uses existing parent-path/delete semantics and ignores test operations', () => {
  const a = item('a', [op('/executor', { timeout: 600 })])
  const { rerender } = render(<RepairProposalChoices items={[a, item('b', [op('/executor/timeout', 90)])]} />)
  expect(screen.getByRole('alert')).toBeInTheDocument()
  rerender(<RepairProposalChoices items={[a, item('b', [op('/executor', undefined, { op: 'remove' })])]} />)
  expect(screen.getByRole('alert')).toHaveTextContent('删除')
  rerender(<RepairProposalChoices items={[a, item('b', [op('/executor', 90, { op: 'test' })])]} />)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
