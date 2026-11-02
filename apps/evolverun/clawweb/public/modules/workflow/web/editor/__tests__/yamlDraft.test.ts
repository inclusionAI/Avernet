import { describe, expect, it } from 'vitest'
import { parseWorkflowYaml, normalizationChanges } from '../yamlDraft'

describe('workflow YAML draft boundary', () => {
  it('accepts missing version without inventing a value and preserves unknown fields', () => {
    const spec = parseWorkflowYaml('id: demo\ntitle: 示例\nnodes: []\ncustom:\n  enabled: false\n  count: 0\n  empty: null\n')
    expect(spec).not.toHaveProperty('version')
    expect(spec.custom).toEqual({ enabled: false, count: 0, empty: null })
  })
  it.each(['', 'null', '[]', 'id: demo\ntitle: 示例\nnodes: [null]', 'id: demo\ntitle: 示例\nnodes:\n - id: a\n   title: A\n   executor: nope'])('rejects malformed documents: %s', text => {
    expect(() => parseWorkflowYaml(text)).toThrow()
  })
  it('rejects duplicate keys rather than silently replacing content', () => {
    expect(() => parseWorkflowYaml('id: a\nid: b\ntitle: A\nnodes: []')).toThrow()
  })
  it('rejects cyclic YAML alias objects', () => {
    expect(() => parseWorkflowYaml('id: a\ntitle: A\nnodes: []\ncustom: &a\n  self: *a')).toThrow(/循环/)
  })
  it('lists removed, changed, and added normalization fields without printing values', () => {
    const original = { id: 'a', version: 2, config: 'raw', custom: false, nodes: [{ id: 'n', outputContract: { required: true } }] }
    const normalized = { id: 'a', version: '2', custom: false, nodes: [{ id: 'n', outputContract: {} }], extra: 1 }
    expect(normalizationChanges(original, normalized)).toEqual(['version', 'config', 'nodes[0].outputContract.required', 'extra'])
  })
  it('returns no changes for identical structured values with different object key order', () => {
    expect(normalizationChanges({ a: 0, b: false }, { b: false, a: 0 })).toEqual([])
  })
})
