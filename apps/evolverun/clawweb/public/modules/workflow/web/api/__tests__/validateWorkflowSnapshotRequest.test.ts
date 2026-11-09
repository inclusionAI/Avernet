import { describe, expect, it } from 'vitest'
import { validateWorkflowSnapshotRequest } from '../validateWorkflowSnapshotRequest'
import { projectWorkflowSnapshotJson } from '../../../server/services/workflow-snapshot-projection'

const node = { id: 'n', title: 'Node', phase: 'execute', executor: { type: 'done' } }
const doc = () => ({ id: 'wf', title: 'Draft', version: '1', nodes: [node] })
const nested = (child: unknown) => ({ id: 'loop', title: 'Loop', phase: 'execute', executor: { type: 'loop-group', body: [child] } })
function assertParity(value: unknown, valid: boolean) {
  const text = JSON.stringify(value)
  if (valid) {
    expect(validateWorkflowSnapshotRequest(text, 'wf')).toEqual(value)
    expect(JSON.parse(projectWorkflowSnapshotJson('wf', text).specJson)).toEqual(value)
  } else {
    expect(() => validateWorkflowSnapshotRequest(text, 'wf')).toThrow()
    expect(() => projectWorkflowSnapshotJson('wf', text)).toThrow()
  }
}
describe('reconciliation save preflight matches server snapshot rules', () => {
  it.each(['all_success', 'one_success', 'all_done'])('accepts triggerRule %s', rule => {
    assertParity({ ...doc(), nodes: [{ ...node, triggerRule: rule }] }, true)
  })
  it.each([
    ['top-level triggerRule', { ...doc(), nodes: [{ ...node, triggerRule: 'invalid' }] }],
    ['null triggerRule', { ...doc(), nodes: [{ ...node, triggerRule: null }] }],
    ['nested triggerRule', { ...doc(), nodes: [nested(nested({ ...node, triggerRule: 'invalid' }))] }],
    ['nested dependsOn', { ...doc(), nodes: [nested({ ...node, dependsOn: ['n', 2] })] }],
    ['legacy loop body', { ...doc(), nodes: [{ ...node, type: 'loop-group', body: [{ ...node, triggerRule: 'invalid' }] }] }],
    ['template nested triggerRule', { ...doc(), nodeTemplates: { x: { body: [nested({ ...node, triggerRule: 'invalid' })] } } }],
    ['template without body', { ...doc(), nodeTemplates: { x: {} } }],
    ['empty loop body', { ...doc(), nodes: [{ ...node, executor: { type: 'loop-group', body: [] } }] }],
    ['null template', { ...doc(), nodeTemplates: null }],
    ['empty nodes', { ...doc(), nodes: [] }],
    ['missing dependency', { ...doc(), nodes: [{ ...node, dependsOn: ['missing'] }] }],
    ['duplicate node', { ...doc(), nodes: [node, node] }],
    ['invalid surrogate', { ...doc(), extra: '\ud800' }],
    ['more than 512 nodes', { ...doc(), nodeTemplates: { x: { body: Array.from({ length: 512 }, (_, i) => ({ ...node, id: `n${i}` })) } } }],
  ])('rejects %s in both client and server', (_label, value) => assertParity(value, false))
  it('accepts structurally valid nested loops and templates', () => {
    assertParity({ ...doc(), nodes: [nested({ ...node, triggerRule: 'all_done' })], nodeTemplates: { x: { body: [node] } } }, true)
  })
  it.each([
    '{"id":"wf","title":"x","nodes":[],"n":9007199254740993}',
    '{"id":"wf","title":"x","title":"y","nodes":[]}',
    '{"id":"wf","title":"x","nodes":[],"n":1e-999}',
  ])('rejects lossy/duplicate raw JSON before parse', text => {
    expect(() => validateWorkflowSnapshotRequest(text, 'wf')).toThrow()
    expect(() => projectWorkflowSnapshotJson('wf', text)).toThrow()
  })
})
