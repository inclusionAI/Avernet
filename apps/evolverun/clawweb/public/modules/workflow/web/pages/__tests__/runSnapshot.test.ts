import { describe, expect, it } from 'vitest'
import { parseRunSnapshot } from '../runSnapshot'

const node = { id: 'n1', title: '', dependsOn: ['previous'], executor: { type: 'action', secret: 'discard' }, extra: 'discard' }

describe('run snapshot topology response', () => {
  it('accepts empty topology and retains only static DAG fields', () => {
    expect(parseRunSnapshot({ nodes: [] })).toEqual([])
    expect(parseRunSnapshot({ nodes: [node] })).toEqual([
      { id: 'n1', title: '', dependsOn: ['previous'], executor: { type: 'action' } },
    ])
  })
  it.each([
    null, {}, { nodes: null }, { nodes: ['node'] }, { nodes: [{ ...node, id: '' }] },
    { nodes: [{ ...node, title: null }] }, { nodes: [{ ...node, dependsOn: null }] },
    { nodes: [{ ...node, dependsOn: [null] }] }, { nodes: [{ ...node, executor: null }] },
    { nodes: [{ ...node, executor: { type: '' } }] }, { nodes: [node, node] },
  ])('rejects malformed snapshots rather than rendering them: %o', response => {
    expect(() => parseRunSnapshot(response)).toThrow()
  })
})
