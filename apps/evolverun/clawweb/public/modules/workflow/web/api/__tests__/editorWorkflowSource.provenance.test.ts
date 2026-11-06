import { createHash, webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { candidateProjection } from '../../../server/services/pack-operation-service'
import { originalActiveDraftBaseline, readEditorWorkflowSource } from '../editorWorkflowSource'
import { historyPackFixture } from './historyPackFixture'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('recovers an actual writer projection by archive contract while binding the exact active token', async () => {
  const authored = { id: 'flow', title: 'Flow', custom: { keep: false }, content: 'user content', nodes: [
    { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
    { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
  ] }
  const yaml = stringify(authored)
  const specJson = candidateProjection('flow', yaml, '{}').specJson
  const row = JSON.parse(specJson)
  expect(row).toEqual({ ...authored, content: yaml })
  // Projection equality is not proof of provenance; recovery follows the
  // explicitly specified history archive contract.
  expect(row.content).not.toBe(authored.content)
  const tokenFor = (text: string) => createHash('sha256').update(JSON.stringify([
    'active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'v7', 'deploy', text,
  ])).digest('hex')
  const { content: _content, ...unambiguous } = authored
  let emittedSpec = JSON.stringify(unambiguous)
  let emittedToken = tokenFor(emittedSpec)
  vi.stubGlobal('crypto', webcrypto)
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url.endsWith('/pack-version?version=current')) return new Response(JSON.stringify(historyPackFixture(emittedToken, yaml)))
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active: {
      deployNumber: 3, version: 7, isActive: true, packId: 'pack', activeToken: emittedToken, tagName: 'v7', action: 'deploy',
    } }))
    if (url.endsWith('/by-deploy/3')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: 'v7', action: 'deploy', specJson: emittedSpec }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const control = await readEditorWorkflowSource('flow')
  expect(originalActiveDraftBaseline(control.spec)?.activeToken).toBe(emittedToken)
  emittedSpec = specJson
  emittedToken = tokenFor(specJson)
  const recovered = await readEditorWorkflowSource('flow')
  expect(recovered.spec).toEqual(authored)
  expect(originalActiveDraftBaseline(recovered.spec)).toMatchObject({ activeToken: emittedToken, specJson })
  emittedToken = 'f'.repeat(64)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('校验不匹配')
  expect(fetchMock).toHaveBeenCalledTimes(18)
})
