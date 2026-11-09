import { expect, it } from 'vitest'
import { packWorkflowBaseline } from '../packCanvas'

const files = (body: string) => [
  { path: 'workflow.pack.yaml', contentBase64: btoa('id: pack\nworkflows: [flow]\n'), mode: '100644' },
  { path: 'workflows/flow.yaml', contentBase64: btoa(body), mode: '100644' },
]
it.each(['9007199254740993', '0.10000000000000001', '-0', '.inf'])('rejects a lossy number in an incomplete Git baseline: %s', value => {
  expect(() => packWorkflowBaseline(files(`id: flow\namount: ${value}\n`), 'flow', 'pack')).toThrow('数字无法无损')
})
it('still accepts a lossless incomplete Git baseline without synthesizing a saved spec', () => {
  expect(packWorkflowBaseline(files('id: flow\namount: 0.1\n'), 'flow', 'pack').spec).toBeNull()
})
