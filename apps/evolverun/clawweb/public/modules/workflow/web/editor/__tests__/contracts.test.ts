import { describe, expect, it } from 'vitest'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { getEffectiveTriggerRule, getResultBranchIds, getResultBranches, hasResultBranches, withResultBranches } from '../contracts'

const node = (patch: Record<string, unknown> = {}): WorkflowNode => ({ id: 'n', title: 'N', phase: 'work', executor: { type: 'done' }, ...patch }) as WorkflowNode

describe('editor runtime contract adapter', () => {
  it('does not add routing/defaults just by reading an unset node', () => {
    const source = node()
    expect(getResultBranches(source)).toEqual([])
    expect(getEffectiveTriggerRule(source)).toBe('all_success')
    expect(source).not.toHaveProperty('onResult')
    expect(source).not.toHaveProperty('triggerRule')
  })
  it.each([null, false, 0, '', [], [{ value: 'yes', target: 'n' }], {}, { if: {}, then: {} },
    { branches: [], then: {} }, { branches: null }, { branches: [{ match: {} }] },
    { branches: [{ branchId: 'b', match: [] }] }, { branches: [{ branchId: 'b', match: {}, complete: 'false' }] },
  ])('keeps unsupported routing %j in raw/compatibility mode', (onResult) => {
    const source = node({ onResult }), before = structuredClone(source)
    expect(getResultBranches(source)).toBeNull()
    expect(() => withResultBranches(source, [])).toThrow()
    expect(source).toEqual(before)
  })
  it('roundtrips modern branches and unknown nested values without shared DTO loss', () => {
    const source = node({ onResult: { branches: [], default: { complete: false }, unknown: [null, 0, false, ''] } })
    const branches = [{ branchId: 'b', match: { yes: true, string: 'true', no: false, zero: 0, empty: '', nothing: null }, complete: false, future: { flags: [0, false] } }]
    const result = { ...source, ...withResultBranches(source, branches) }
    expect(JSON.parse(JSON.stringify(result)).onResult).toEqual({ branches, default: { complete: false }, unknown: [null, 0, false, ''] })
    branches[0].future.flags[0] = 99
    expect(getResultBranches(result)![0].future).toEqual({ flags: [0, false] })
    expect(getResultBranches(source)).toEqual([])
  })
  it.each(['all_success', 'one_success', 'all_done'] as const)('explicit %s overrides join:any', (triggerRule) => {
    expect(getEffectiveTriggerRule(node({ triggerRule, join: 'any' }))).toBe(triggerRule)
  })
  it('supports the legacy join shorthand', () => {
    expect(getEffectiveTriggerRule(node({ join: 'any' }))).toBe('one_success')
    expect(getEffectiveTriggerRule(node({ join: 'all' }))).toBe('all_success')
  })
})


describe('branch reference extraction independent of UI compatibility', () => {
  it('extracts named IDs while leaving anonymous entries and complete flags intact', () => {
    const source = node({ onResult: { branches: [
      { branchId: 'yes', match: { approved: true }, complete: true, future: null },
      { match: {}, complete: true, future: [false, 0, null] },
    ] } })
    const before = structuredClone(source)
    expect(getResultBranches(source)).toBeNull()
    expect(hasResultBranches(source)).toBe(true)
    expect(getResultBranchIds(source)).toEqual(['yes'])
    expect(() => withResultBranches(source, [])).toThrow()
    expect(source).toEqual(before)
  })
  it('does not drop known named references due to unsupported sibling values', () => {
    const source = node({ onResult: { branches: [null, false, { branchId: 'yes', match: [] }, { branchId: 0 }] } })
    expect(getResultBranches(source)).toBeNull()
    expect(getResultBranchIds(source)).toEqual(['yes'])
  })
  it('treats anonymous-only routing as an origin boundary with no named IDs', () => {
    const source = node({ onResult: { branches: [{ match: {}, complete: true }] } })
    expect(getResultBranchIds(source)).toEqual([])
    expect(hasResultBranches(source)).toBe(true)
  })
  it.each([undefined, null, [], { if: {}, then: {} }, { branches: [] }, { branches: null }])(
    'does not invent named branch origins for %j', onResult => {
      expect(getResultBranchIds(node({ onResult }))).toEqual([])
      expect(hasResultBranches(node({ onResult }))).toBe(false)
    },
  )
})
