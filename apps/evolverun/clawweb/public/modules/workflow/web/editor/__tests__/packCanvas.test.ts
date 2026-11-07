import { expect, it } from 'vitest'
import { stringify } from 'yaml'
import { mergeCanvasFiles, packManifest, packWorkflow } from '../packCanvas'
import type { Snapshot } from '../packVersionSession'
import type { PackVersionFile } from '../../components/PackVersionEditor'

const spec = { id: 'workflow', title: 'Workflow', version: '1', nodes: [] }
const file = (path: string, value: unknown): PackVersionFile => ({ path, mode: '100644', contentBase64: btoa(String.fromCharCode(...new TextEncoder().encode(stringify(value)))) })
function files(packId: unknown = 'pack'): PackVersionFile[] {
  return [file('workflow.pack.yaml', { id: packId, workflows: [{ id: spec.id, file: 'flows/custom.yaml' }] }), file('flows/custom.yaml', spec)]
}
const snapshot: Snapshot = { workflowId: spec.id, packId: 'pack', baseRef: 'refs/tags/deploy/workflow/#1', baseCommit: 'a'.repeat(40), draftHead: null, versionFiles: files() }

it.each(['renamed', 'new-pack', 'new_pack.v2', 'A'.repeat(128)])('accepts a legal candidate Pack ID %s without weakening read snapshot validation', packId => {
  expect(packManifest(files(packId), spec.id)).toEqual({ packId, path: 'flows/custom.yaml' })
  expect(packWorkflow(files(packId), spec.id)).toMatchObject({ packId, spec })
  expect(() => packWorkflow(files(packId), spec.id, snapshot.packId)).toThrow('身份与 Git 基线不一致')
})
it.each(['', '.', '..', '../other', 'pack/other', 'pack\\other', 'pack..id', '.hidden', '-leading', 'pack.', 'pack.lock',
  'A'.repeat(129), 'bad id', 'pack\n', '包', 123, null, [], {}])('rejects an unsafe or non-string manifest Pack ID %j', packId => {
  expect(() => packManifest(files(packId), spec.id)).toThrow('ID 无效')
})
it('merges a rename and changed manifest workflow path without touching other resources', () => {
  const resource: PackVersionFile = { path: 'assets/raw', mode: '100755', contentBase64: 'AP8C' }
  const candidate = [file('workflow.pack.yaml', { id: 'renamed', workflows: [{ id: spec.id, file: 'next/workflow.yaml' }] }),
    file('next/workflow.yaml', spec), resource]
  const merged = mergeCanvasFiles(snapshot, candidate, spec, { ...spec, title: 'Edited' })
  expect(packWorkflow(merged, spec.id)).toMatchObject({ packId: 'renamed', path: 'next/workflow.yaml', spec: { ...spec, title: 'Edited' } })
  expect(merged[0]).toBe(candidate[0]); expect(merged[2]).toBe(resource)
})
it('still binds the manifest workflow entry to the unchanged workflow ID', () => {
  const candidate = [file('workflow.pack.yaml', { id: 'renamed', workflows: [{ id: 'other', file: 'flows/custom.yaml' }] }), files()[1]]
  expect(() => mergeCanvasFiles(snapshot, candidate, spec, spec)).toThrow('唯一声明当前工作流')
})
it('still rejects a renamed candidate whose YAML changes workflow identity', () => {
  const candidate = [files('renamed')[0], file('flows/custom.yaml', { ...spec, id: 'other' })]
  expect(() => mergeCanvasFiles(snapshot, candidate, spec, spec)).toThrow('工作流 YAML 身份')
})
it('does not infer identity from missing, duplicate or duplicate-key manifests', () => {
  expect(() => packManifest([files()[1]], spec.id)).toThrow()
  expect(() => packManifest([files()[0], ...files()], spec.id)).toThrow()
  const duplicate = { ...files()[0], contentBase64: btoa('id: pack\nid: renamed\nworkflows: [workflow]\n') }
  expect(() => packManifest([duplicate, files()[1]], spec.id)).toThrow()
})
