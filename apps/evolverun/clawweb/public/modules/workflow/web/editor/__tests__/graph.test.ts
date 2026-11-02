import { describe, expect, it } from 'vitest'
import type { WorkflowNode, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { branchEditError, canConnect, computeDagLayout, findNodeReferences, validateSpec } from '../graph'
import { withResultBranches } from '../contracts'

const node = (id: string, patch: Record<string, unknown> = {}): WorkflowNode => ({ id, title: id, phase: 'work', executor: { type: 'future-extension' }, ...patch }) as WorkflowNode
const spec = (nodes: WorkflowNode[]): WorkflowSpec => ({ id: 'flow', title: 'Flow', version: '1', nodes })
const router = (id = 'r', branchId = 'yes') => ({ ...node(id), ...withResultBranches(node(id), [{ branchId, match: { ok: true } }]) })

describe('safe structural validation and bounded layout', () => {
  it('allows a missing version and unknown executor without writing defaults', () => {
    const source = spec([node('a')]); delete source.version
    expect(validateSpec(source)).toEqual([])
    expect(source).not.toHaveProperty('version')
    expect(source.nodes[0].executor.type).toBe('future-extension')
  })
  it('reports missing phase but never invents one', () => {
    const source = spec([node('a')]); delete source.nodes[0].phase
    expect(validateSpec(source)).toContainEqual(expect.objectContaining({ nodeId: 'a', message: expect.stringContaining('phase') }))
    expect(source.nodes[0]).not.toHaveProperty('phase')
  })
  it('reports malformed structure, duplicate IDs, dangling edges and cycles', () => {
    expect(validateSpec({ ...spec([]), nodes: null })).toContainEqual(expect.objectContaining({ type: 'invalid-structure' }))
    expect(validateSpec(spec([null]))).toContainEqual(expect.objectContaining({ type: 'invalid-structure' }))
    const errors = validateSpec(spec([node('a', { dependsOn: ['a', 'missing', 'missing'] }), node('a')]))
    expect(errors.map((error) => error.type)).toEqual(expect.arrayContaining(['duplicate-id', 'invalid-edge', 'missing-reference']))
    expect(validateSpec(spec([node('a', { dependsOn: ['a'] })])).map((error) => error.type)).toContain('cycle')
  })
  it('lays out deterministic DAG levels and every disconnected node', () => {
    const nodes = [node('a'), node('b', { dependsOn: ['a'] }), node('c', { dependsOn: ['a'] }), node('d', { dependsOn: ['b', 'c'] }), node('e')]
    const result = computeDagLayout(nodes)
    expect(result).toEqual(computeDagLayout(nodes))
    expect(result.size).toBe(5)
    expect(result.get('a')!.y).toBeLessThan(result.get('b')!.y)
    expect(result.get('b')!.y).toBe(result.get('c')!.y)
    expect(result.get('c')!.y).toBeLessThan(result.get('d')!.y)
    expect(new Set([...result.values()].map((point) => JSON.stringify(point))).size).toBe(5)
  })
  it('places cyclic and dangling inputs without recursion', () => {
    const nodes = [node('a', { dependsOn: ['b'] }), node('b', { dependsOn: ['a'] }), node('c', { dependsOn: ['missing'] }), node('d', { dependsOn: ['a'] })]
    const result = computeDagLayout(nodes)
    expect(result.size).toBe(4)
    expect([...result.values()].every(({ x, y }) => Number.isFinite(x) && Number.isFinite(y))).toBe(true)
    expect(validateSpec(spec(nodes)).map((error) => error.type)).toEqual(expect.arrayContaining(['cycle', 'missing-reference']))
  })
  it('handles a deep chain and a large cycle with bounded iterative traversals', () => {
    const nodes = Array.from({ length: 12000 }, (_, i) => node(`n${i}`, { dependsOn: i ? [`n${i - 1}`] : [] }))
    expect(computeDagLayout(nodes).size).toBe(nodes.length)
    expect(validateSpec(spec(nodes))).toEqual([])
    nodes[0].dependsOn = ['n11999']
    expect(computeDagLayout(nodes).size).toBe(nodes.length)
    expect(validateSpec(spec(nodes)).some((error) => error.type === 'cycle')).toBe(true)
  })
})

describe('connection and branch safety', () => {
  it.each([
    ['a', 'a', 'self'], ['missing', 'a', 'missing'], ['a', 'missing', 'missing'],
    ['a', 'b', 'duplicate'], ['c', 'a', 'cycle'],
  ])('rejects %s -> %s (%s)', (source, target, error) => {
    const flow = spec([node('a'), node('b', { dependsOn: ['a'] }), node('c', { dependsOn: ['b'] })])
    expect(canConnect(flow, source, target)).toContain(error)
  })
  it('allows a non-cyclic missing edge even alongside an imported cyclic component', () => {
    const flow = spec([node('a', { dependsOn: ['b'] }), node('b', { dependsOn: ['a'] }), node('c'), node('d')])
    expect(canConnect(flow, 'c', 'd')).toBeNull()
  })
  it('requires a real branch port and protects existing target membership without interpreting complete', () => {
    const flow = spec([router(), node('target'), node('other', { branchId: 'no' })])
    expect(canConnect(flow, 'r', 'target')).not.toBeNull()
    expect(canConnect(flow, 'r', 'target', 'unknown')).not.toBeNull()
    expect(canConnect(flow, 'r', 'target', 'yes')).toBeNull()
    expect(canConnect(flow, 'r', 'other', 'yes')).not.toBeNull()
    Object.assign(flow.nodes[0], withResultBranches(flow.nodes[0], [{ branchId: 'yes', match: {}, complete: true }]))
    expect(canConnect(flow, 'r', 'target', 'yes')).toBeNull()
    expect(canConnect(flow, 'r', 'other', 'yes')).not.toBeNull()
  })
  it.each([true, false])('validates referenced branches with complete=%s without rewriting the flag', (complete) => {
    const routing = { branches: [{ branchId: 'yes', match: { ok: true }, complete, future: { zero: 0, nullable: null } }] }
    const flow = spec([node('r', { onResult: routing }), node('child', { dependsOn: ['r'], branchId: 'yes' })])
    const original = structuredClone(flow)
    expect(validateSpec(flow)).toEqual([])
    expect(flow).toEqual(original)
  })
  it.each([true, false])('allows toggling complete to %s for direct and transitive branch members', (complete) => {
    const before = spec([
      node('r', { onResult: { branches: [{ branchId: 'yes', match: {}, complete: !complete }] } }),
      node('child', { branchId: 'yes', dependsOn: ['r'] }),
      node('descendant', { branchId: 'yes', dependsOn: ['child'] }),
    ])
    const after = structuredClone(before)
    Object.assign(after.nodes[0], withResultBranches(after.nodes[0], [{ branchId: 'yes', match: {}, complete }]))
    expect(branchEditError(before, after)).toBeNull()
    expect(validateSpec(after)).toEqual([])
  })
  it('rejects branch conflicts against existing upstream routing', () => {
    const flow = spec([router(), router('other', 'no'), node('target', { dependsOn: ['other'] })])
    expect(canConnect(flow, 'r', 'target', 'yes')).not.toBeNull()
  })
  it('does not erase membership when adding an ordinary dependency', () => {
    expect(canConnect(spec([node('a'), node('b', { branchId: 'yes' })]), 'a', 'b')).toBeNull()
  })
  it('checks duplicate branch IDs and fallback order', () => {
    expect(validateSpec(spec([router(), router('other')])).some((error) => error.type === 'branch-conflict')).toBe(true)
    const routing = { ...node('r'), ...withResultBranches(node('r'), [{ branchId: 'fallback', match: {} }, { branchId: 'yes', match: { ok: true } }]) }
    expect(validateSpec(spec([routing])).some((error) => error.message.includes('最后'))).toBe(true)
  })
  it.each([
    [], [{ branchId: 'renamed', match: {} }],
  ])('blocks routing edits stranding a transitive branch member: %j', (branches) => {
    const before = spec([router(), node('middle'), node('child', { branchId: 'yes', dependsOn: ['middle'] })])
    const after = structuredClone(before)
    Object.assign(after.nodes[0], withResultBranches(after.nodes[0], branches))
    expect(branchEditError(before, after)).toContain('child')
  })
})

describe('known node reference blockers', () => {
  it.each([
    ['output template', { executor: { type: 'embedded-agent', prompt: '{{nodeOutput.a.answer}}' } }],
    ['nodes template', { executor: { type: 'human', prompt: '{{ nodes.a.output }}' } }],
    ['bracket template', { progressMessage: '{{nodeOutput["a"].answer}}' }],
    ['feedback', { onFeedback: { target: 'a' } }],
    ['collaboration feedback', { executor: { type: 'collaboration', onFeedback: { target: 'a' } } }],
    ['human revise', { executor: { type: 'human', actions: { revise: { target: 'a' } } } }],
    ['approval revise', { executor: { type: 'approval', onRevise: { target: 'a' } } }],
    ['conditional rerun', { onResult: { if: { ok: false }, then: { rerun: { target: 'a' } } } }],
    ['legacy branch target', { onResult: [{ value: 'yes', target: 'a' }] }],
    ['condition key', { skipWhen: { 'nodeOutput.a.ok': false } }],
  ])('finds %s and reports the owning source', (_name, patch) => {
    const refs = findNodeReferences(spec([node('a'), node('b', patch)]), 'a')
    expect(refs.length).toBeGreaterThan(0)
    expect(refs.some((ref) => ref.includes('nodes[b]'))).toBe(true)
  })
  it('finds workflow outputs, not only node fields', () => {
    expect(findNodeReferences({ ...spec([node('a')]), outputs: { result: '{{nodeOutput.a}}' } }, 'a')).toContain('spec.outputs.result')
  })
  it('does not confuse plain strings, external route targets, prefixes or self-references with blockers', () => {
    const source = spec([node('a', { progressMessage: '{{nodeOutput.a.answer}}' }), node('b', {
      title: 'a', executor: { type: 'bcs-route', target: 'a', message: '{{nodeOutput.ab.answer}} plain a' }, dependsOn: ['a'],
    })])
    expect(findNodeReferences(source, 'a')).toEqual([])
  })
  it('does not interpret a branch match value or arbitrary extension target as a node reference', () => {
    const source = spec([node('a'), node('b', {
      onResult: { branches: [{ branchId: 'yes', match: { target: 'a' } }] },
      executor: { type: 'mcp-call', args: { actions: [{ target: 'a' }] } },
      extension: { onFeedback: { target: 'a' } },
    })])
    expect(findNodeReferences(source, 'a')).toEqual([])
  })
  it('escapes regexp punctuation in node IDs', () => {
    expect(findNodeReferences(spec([node('a+b'), node('b', { progressMessage: '{{nodeOutput.a+b.answer}}' })]), 'a+b')).toHaveLength(1)
    expect(findNodeReferences(spec([node('a+b'), node('b', { progressMessage: '{{nodeOutput.aaab.answer}}' })]), 'a+b')).toEqual([])
  })
  it('blocks deleting the producer of transitive branch membership', () => {
    expect(findNodeReferences(spec([router(), node('b', { branchId: 'yes' })]), 'r')).toContain('spec.nodes[b].branchId (yes)')
  })
})
