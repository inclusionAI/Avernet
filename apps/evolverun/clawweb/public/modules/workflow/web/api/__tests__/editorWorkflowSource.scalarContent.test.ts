import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { readEditorWorkflowSource } from '../editorWorkflowSource'

const doc = { id: 'flow', title: 'Active', version: 'v2', nodes: [], content: 'user payload', custom: { keep: [false, 0, null] } }
const active = { deployNumber: 2, version: 3, isActive: true }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function setup(specJson: string) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
    if (url.endsWith('/by-deploy/2')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson }))
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

it.each([JSON.stringify(doc), stringify(doc), JSON.stringify(stringify(doc))])('does not infer content provenance from simple characters or encoding', async snapshot => {
  const fetchMock = setup(snapshot)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it.each([stringify(doc), JSON.stringify(doc)])('does not unwrap even a matching archive without an active token: %s', async content => {
  setup(JSON.stringify({ ...doc, content }))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
})

it.each([
  JSON.stringify(stringify(doc)), 'nodes: [', 'id: other\nnodes: []',
])('refuses content that may encode a document, even in a complete outer snapshot: %s', async content => {
  setup(JSON.stringify({ ...doc, content }))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
})

it('does not treat a scalar content-only envelope as a complete workflow', async () => {
  setup(JSON.stringify({ id: 'flow', content: 'user payload' }))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
})
