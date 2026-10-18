import { expect, it } from 'vitest'
import { reviewedSelection } from '../repair-selection-review'
import { exclusion } from '../repair-view'
import { proposalConflicts, proposalPending } from '../../../../../server/contracts/repair-proposal'
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
const item = (id: string, digest = 'a') => ({ itemId: id, proposal: { summary: id }, instruction: '', contentRevision: 1, proposalKey: id,
  sourceAvailable: true, state: 'pending', selectionDigest: digest.repeat(64) }) as RepairInboxItem
it('retains reviewed original IDs across regrouping and removes only changed or unavailable originals', () => {
  const originals = Object.fromEntries(['same', 'changed', 'adopted', 'missing'].map(id => [id, item(id)]))
  const fresh = { items: [item('same'), item('changed', 'b'), { ...item('adopted'), state: 'awaiting_verification' as const }, item('unselected')], missing: ['missing'], inputDigest: 'new', canEdit: true }
  const result = reviewedSelection(Object.keys(originals), originals, fresh)
  expect(result.retained).toEqual(['same'])
  expect(result.notice).toContain('建议内容已变化')
  expect(result.notice).toContain('正在等待效果验证')
  expect(result.notice).toContain('原始来源已不可用')
  expect(result.retained).not.toContain('unselected')
})
it.each([
  ['awaiting_verification', '等待效果验证'], ['verified', '已通过现有验证规则'], ['ineffective', '未达预期'], ['processing', '正在处理'],
])('explains lifecycle exclusion %s', (state, reason) => expect(exclusion({ ...item('old'), state } as RepairInboxItem)).toContain(reason))
it('retains current complementary-plan authorization without introducing false conflicts on raw original refresh', () => {
  const a = item('a'), b = item('b');
  a.proposal!.operations = [{ op: 'replace', nodeId: 'report', path: '/prompt', value: 'goal A' }]
  b.proposal!.operations = [{ op: 'replace', nodeId: 'report', path: '/prompt', value: 'goal B' }]
  const plan = { id: 'plan', fingerprint: 'current', itemIds: ['a', 'b'], title: 'both goals', changes: ['A and B'], relation: 'complementary' as const, status: 'ready' as const, conflicts: [] }
  const aggregation = { ...plan, originals: [a, b], flowIds: [] }
  const previous = { a: { ...a, aggregation }, b: { ...b, aggregation } }
  const fresh = { items: [a, b], plans: [plan], missing: [], inputDigest: 'new', canEdit: true }
  const result = reviewedSelection(['a', 'b'], previous, fresh)
  expect(result.retained).toEqual(['a', 'b']);expect(result.planReview).toBe(false)
  expect(proposalConflicts(Object.values(result.current))).toHaveLength(0)
  expect(proposalPending(Object.values(result.current))).toBe(false)
  const changed = reviewedSelection(['a', 'b'], previous, { ...fresh, plans: [{ ...plan, id: 'expanded', itemIds: ['a', 'b', 'unseen'] }] }, true)
  expect(changed.retained).toEqual(['a', 'b']);expect(changed.planReview).toBe(true)
  expect(changed.retained).not.toContain('unseen')
})
it('keeps a single unchanged original independent of unrelated queued or expanded group plans', () => {
  const b = item('b')
  const oldPlan = { id: 'old', fingerprint: 'old', itemIds: ['b'], title: 'b', changes: [], relation: 'exact' as const, status: 'queued' as const, conflicts: [], originals: [b], flowIds: [] }
  for (const plan of [
    { ...oldPlan, id: 'new', fingerprint: 'unselected-change' },
    { ...oldPlan, id: 'expanded', fingerprint: 'expanded', itemIds: ['b', 'unselected'], relation: 'complementary' as const, status: 'ready' as const },
  ]) {
    const result = reviewedSelection(['b'], { b: { ...b, aggregation: oldPlan } }, { items: [b], plans: [plan], missing: [], inputDigest: 'new', canEdit: true })
    expect(result.retained).toEqual(['b']);expect(result.planReview).toBe(false)
    expect(proposalPending(Object.values(result.current))).toBe(false)
  }
})
