import { createHash, webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { parse, stringify } from 'yaml'
import { candidateProjection } from '../../../server/services/pack-operation-service'
import { readEditorWorkflowSource } from '../editorWorkflowSource'
import { parseWorkflowYaml } from '../../editor/yamlDraft'
import { historyPackFixture } from './historyPackFixture'

const authored = { id: 'flow', title: '工作流', content: '用户正文',
  __clawweb_snapshot_v1: { version: 1, contentSource: 'authored' }, custom: { nested: [false, 0, null, '中文'] }, nodes: [
    { id: 'start', title: '开始', executor: { type: 'start' }, dependsOn: [] },
    { id: 'end', title: '结束', executor: { type: 'end' }, dependsOn: ['start'] },
  ] }
const { __clawweb_snapshot_v1: _marker, ...authoredWithoutMarker } = authored
const projection = candidateProjection('flow', stringify(authoredWithoutMarker), '{}').specJson

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function useActiveSnapshot(specJson: string, gitYaml?: string) {
  vi.stubGlobal('crypto', webcrypto)
  const activeToken = createHash('sha256').update(JSON.stringify([
    'active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'v7', 'deploy', specJson,
  ])).digest('hex')
  const active = { deployNumber: 3, version: 7, isActive: true, packId: 'pack', activeToken, tagName: 'v7', action: 'deploy' }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url.endsWith('/pack-version?version=current')) return new Response(JSON.stringify(historyPackFixture(activeToken, gitYaml ?? JSON.parse(specJson).content)))
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
    if (url.endsWith('/by-deploy/3')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: 'v7', action: 'deploy', specJson }))
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

it.each([true, false])('recovers an exact writer projection under the archive contract and preserves inner content; marker=%s', async withMarker => {
  const { __clawweb_snapshot_v1: _marker, ...withoutMarker } = authored
  const source = withMarker ? authored : withoutMarker
  const specJson = candidateProjection('flow', stringify(source), '{}').specJson
  const outer = JSON.parse(specJson)
  const inner = parse(outer.content)
  expect(inner).toEqual(source)
  // Equality checks the archive contract, not the provenance of the row.
  expect(outer).toEqual({ ...inner, content: outer.content })
  expect(parseWorkflowYaml(specJson)).toEqual(outer)
  expect(outer.content).not.toBe(inner.content)
  const fetchMock = useActiveSnapshot(specJson)
  expect((await readEditorWorkflowSource('flow')).spec).toEqual(source)
  expect(fetchMock).toHaveBeenCalledTimes(8)
})

it.each(['extra', 'missing', 'conflict', 'nested-conflict', 'invalid-yaml', 'inner-id'] as const)('fails closed on a %s projection', async kind => {
  const outer = JSON.parse(projection)
  if (kind === 'extra') outer.unexpected = '保留'
  if (kind === 'missing') delete outer.custom
  if (kind === 'conflict') outer.title = '外层不同'
  if (kind === 'nested-conflict') outer.custom = { ...outer.custom, nested: [false, 0, null, '不同'] }
  if (kind === 'invalid-yaml') outer.content = 'nodes: ['
  if (kind === 'inner-id') outer.content = stringify({ ...authoredWithoutMarker, id: 'other' })
  const fetchMock = useActiveSnapshot(JSON.stringify(outer), stringify(authoredWithoutMarker))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow(kind === 'invalid-yaml' ? '格式无效' : kind === 'inner-id' ? '身份不匹配' : '不一致')
  expect(fetchMock).toHaveBeenCalledTimes(4)
})


it('accepts reordered object keys without changing arrays or the raw history row', async () => {
  const original = JSON.parse(projection)
  const reordered = { content: original.content, nodes: original.nodes, custom: original.custom,
    title: original.title, id: original.id }
  const specJson = JSON.stringify(reordered)
  const fetchMock = useActiveSnapshot(specJson)
  const source = await readEditorWorkflowSource('flow')
  expect(source.spec).toEqual(authoredWithoutMarker)
  expect(JSON.parse(specJson)).toEqual(reordered)
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
})
