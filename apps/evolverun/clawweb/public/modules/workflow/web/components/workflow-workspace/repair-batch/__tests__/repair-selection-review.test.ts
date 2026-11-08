import { expect, it } from 'vitest'
import { reviewedSelection } from '../repair-selection-review'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
const item = (id: string, digest = 'a') => ({ itemId: id, proposal: { summary: id }, instruction: '', contentRevision: 1,
  proposalKey: id, sourceAvailable: true, state: 'pending', selectionDigest: digest.repeat(64) }) as RepairInboxItem
it('preserves exactly the chosen IDs through content updates, regrouping and unavailable reads', () => {
  const previous = { a: item('a'), b: item('b') }
  const fresh = { items: [item('a', 'b'), item('new')], missing: ['b'], inputDigest: 'new', canEdit: true }
  const result = reviewedSelection(['a', 'b'], previous, fresh)
  expect(result.retained).toEqual(['a', 'b'])
  expect(Object.keys(result.current)).toEqual(['a', 'b'])
  expect(result.current.a.selectionDigest).toBe('b'.repeat(64))
  expect(result.current.b).toEqual(previous.b)
  expect(result.planReview).toBe(false)
  expect(result.notice).toBe('')
})
it('does not import unselected members or require plan confirmation', () => {
  const a = item('a'), b = item('b')
  const plan = { id: 'plan', fingerprint: 'new', itemIds: ['a', 'b'], title: 'plan', changes: [],
    relation: 'complementary' as const, status: 'queued' as const, originals: [a, b], flowIds: [], conflicts: [] }
  const previous = { a: { ...a, aggregation: plan } }
  const result = reviewedSelection(['a'], previous, { items: [a, b], plans: [plan], missing: [], inputDigest: 'new', canEdit: true })
  expect(result.retained).toEqual(['a'])
  expect(Object.keys(result.current)).toEqual(['a'])
  expect(result.current.a.aggregation).toBeUndefined()
  expect(result.planReview).toBe(false)
})

it('retains a verified historical suggestion as a read-only goal for the current target', () => {
  const snapshot = { kind: 'deployment' as const, workflowId: 'wf', workflowVersion: 39, deployNumber: 34, configDigest: 'a'.repeat(64) }
  const old = { ...item('old'), state: 'verified' as const, context: { sourceSnapshot: { status: 'resolved', snapshot } } }
  const fresh = { items: [old], missing: [], inputDigest: 'new', canEdit: true,
    activeSnapshot: { status: 'resolved' as const, snapshot: { ...snapshot, workflowVersion: 40, deployNumber: 35 } } }
  expect(reviewedSelection(['old'], { old }, fresh).retained).toEqual(['old'])
})
