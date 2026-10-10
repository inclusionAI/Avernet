import { expect, it } from 'vitest'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
import { selectionConflicts } from '../repair-selection'

const item = (id: string, operations: unknown[]): RepairInboxItem => ({
  itemId: id, proposal: { operations },
} as RepairInboxItem)
it('allows independent changes but identifies different values on the same target across problems', () => {
  const timeout = item('a', [{ nodeId: 'fetch', path: '/executor/timeoutMs', value: 600 }])
  const retry = item('b', [{ nodeId: 'fetch', path: '/retry/maxAttempts', value: 2 }])
  expect(selectionConflicts([timeout, retry])).toEqual([])
  expect(selectionConflicts([timeout, item('c', [{ nodeId: 'fetch', path: '/executor/timeoutMs', value: 90 }])]))
    .toEqual([{ itemIds: ['a', 'c'], nodeId: 'fetch', path: '/executor/timeoutMs' }])
})
it('checks overlapping parent paths and delete operations without treating equal JSON as conflicting', () => {
  const parent = item('a', [{ nodeId: 'fetch', op: 'replace', path: '/executor', value: { a: 1, b: 2 } }])
  expect(selectionConflicts([parent, item('same', [{ nodeId: 'fetch', op: 'replace', path: '/executor', value: { b: 2, a: 1 } }])])).toEqual([])
  expect(selectionConflicts([parent, item('child', [{ nodeId: 'fetch', path: '/executor/timeoutMs', value: 90 }])])).toHaveLength(1)
  expect(selectionConflicts([parent, item('delete', [{ nodeId: 'fetch', op: 'remove', path: '/executor' }])])).toHaveLength(1)
  expect(selectionConflicts([parent, item('other-node', [{ nodeId: 'write', path: '/executor', value: 90 }])])).toEqual([])
})
