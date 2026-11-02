import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { readEditorWorkflowSource } from '../editorWorkflowSource'
import { useEditorWorkflowDocument } from '../useEditorWorkflowDocument'
import { useEditorStore } from '../../editor/store'

const draft: WorkflowSpec = { id: 'flow', title: 'Database draft', version: 'draft-source', nodes: [], custom: { keep: false } }
const deployed: WorkflowSpec = { id: 'flow', title: 'Active', version: '2.7.0', nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' } }], custom: { zero: 0 } }
const active = { deployNumber: 3, version: 7, isActive: true, action: 'deploy', tagName: 'v7', fromDeployNumber: null, note: null, botId: null, ownerId: null, gmtCreate: 1 }
const snapshot = (specJson = JSON.stringify(deployed)) => ({ workflowId: 'flow', version: 7, deployNumber: 3, tagName: 'v7', action: 'deploy', specJson, note: null, gmtCreate: 1 })
const missing = () => new Response(JSON.stringify({ error: 'Not Found', message: 'Workflow "flow" not found' }), { status: 404 })
let request: ReturnType<typeof vi.fn>
const clients: QueryClient[] = []
beforeEach(() => {
  useEditorStore.getState().reset({ force: true })
  request = vi.fn(async (url: string) => {
    if (url === '/api/workflows/flow') return missing()
    if (url === '/api/workflows/flow/history?limit=50&releaseOnly=true') return new Response(JSON.stringify({ workflowId: 'flow', history: [{ ...active, deployNumber: 100, version: 100, isActive: false }], active }))
    if (url === '/api/workflows/flow/history/by-deploy/3') return new Response(JSON.stringify(snapshot()))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', request)
})
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('editor source uses DB draft before an explicitly active DB deployment', () => {
  it('uses the complete draft without any history, snapshot or Git request', async () => {
    request.mockResolvedValueOnce(new Response(JSON.stringify(draft)))
    const source = await readEditorWorkflowSource('flow')
    expect(source).toEqual({ spec: draft, databaseSpec: draft, activeDeployNumber: null })
    expect(request).toHaveBeenCalledTimes(1)
    expect(source.spec).not.toBe(source.databaseSpec)
  })
  it.each(['direct', 'export', 'projection'] as const)('preserves a null version from an existing draft via %s without fallback', async kind => {
    const legacy = { ...draft, version: null }
    const response = kind === 'direct' ? legacy : { ...legacy, version: 19, ...(kind === 'projection' ? { content: stringify(legacy) } : {}) }
    request.mockResolvedValueOnce(new Response(JSON.stringify(response)))
    if (kind !== 'direct') request.mockResolvedValueOnce(new Response(stringify(kind === 'projection' ? { ...legacy, version: '1', content: stringify(legacy) } : legacy)))
    const result = await readEditorWorkflowSource('flow')
    expect(result).toEqual({ spec: legacy, databaseSpec: legacy, activeDeployNumber: null })
    expect(result.spec.version).toBeNull(); expect(result.databaseSpec!.version).toBeNull()
    expect(request.mock.calls.map(([url]) => url)).toEqual(kind === 'direct' ? ['/api/workflows/flow'] : ['/api/workflows/flow', '/api/workflows/flow/export'])
  })
  it.each(['json', 'wrapper', 'projection'] as const)('preserves null version in an active %s document only when the draft is absent', async kind => {
    const legacy = { ...deployed, version: null }, yaml = stringify(legacy)
    const specJson = JSON.stringify(kind === 'json' ? legacy : kind === 'wrapper' ? { content: yaml } : { ...legacy, version: '1', content: yaml })
    const transport = request.getMockImplementation()!
    request.mockImplementation((url: string) => url.endsWith('/by-deploy/3') ? Promise.resolve(new Response(JSON.stringify(snapshot(specJson)))) : transport(url))
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: legacy, databaseSpec: null, activeDeployNumber: 3 })
  })
  it('uses the top-level active record even when it is older than the history page', async () => {
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: deployed, databaseSpec: null, activeDeployNumber: 3 })
    expect(request.mock.calls.map(([url]) => url)).toEqual(['/api/workflows/flow', '/api/workflows/flow/history?limit=50&releaseOnly=true', '/api/workflows/flow/history/by-deploy/3'])
  })
  it.each(['json', 'yaml', 'wrapper', 'projection', 'numeric', 'absent-version', 'custom-content'] as const)('preserves the source document from %s', async kind => {
    const original = structuredClone(deployed)
    if (kind === 'numeric') original.version = 2.7 as unknown as string
    if (kind === 'absent-version') delete original.version
    if (kind === 'custom-content') original.content = stringify({ ...deployed, version: 'other-source' })
    const yaml = stringify(original)
    const specJson = kind === 'yaml' ? yaml : JSON.stringify(kind === 'wrapper' ? { content: yaml }
      : ['projection', 'numeric', 'absent-version'].includes(kind) ? { ...original, version: String(original.version ?? '1'), content: yaml } : original)
    const transport = request.getMockImplementation()!
    request.mockImplementation((url: string) => url.endsWith('/by-deploy/3') ? Promise.resolve(new Response(JSON.stringify(snapshot(specJson)))) : transport(url))
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: original, databaseSpec: null, activeDeployNumber: 3 })
  })
  it.each([
    [404, { error: 'Not Found' }], [404, { error: 'Not Found', message: 'Workflow "other" not found' }],
    [404, { code: 'OTHER_NOT_FOUND' }], [404, '<html>not found</html>'], [503, { code: 'WORKFLOW_NOT_FOUND' }], [403, {}],
  ])('does not treat ambiguous or unrelated failures as draft absence (%s %j)', async (status, body) => {
    useEditorStore.getState().loadSpec(draft)
    useEditorStore.getState().updateSpecField('title', 'Local unsaved')
    const before = useEditorStore.getState()
    request.mockResolvedValueOnce(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status }))
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
    expect(request).toHaveBeenCalledTimes(1)
    expect(useEditorStore.getState()).toBe(before)
  })
  it('accepts an explicit WORKFLOW_NOT_FOUND response', async () => {
    request.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 }))
    expect((await readEditorWorkflowSource('flow')).databaseSpec).toBeNull()
  })
  it('does not fallback after a draft export failure, even for the exact missing response', async () => {
    request.mockResolvedValueOnce(new Response(JSON.stringify({ ...draft, version: 19 }))).mockResolvedValueOnce(missing())
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow('原文')
    expect(request.mock.calls.map(([url]) => url)).toEqual(['/api/workflows/flow', '/api/workflows/flow/export'])
  })
  it.each(['network', 'malformed-draft', 'history-failure', 'missing-active', 'history-identity', 'snapshot-identity', 'snapshot-number', 'snapshot-version', 'snapshot-structure', 'spec-identity'] as const)('fails closed without changing the current draft: %s', async kind => {
    useEditorStore.getState().loadSpec(draft)
    useEditorStore.getState().setPendingEdit('yaml', 'Unapplied')
    const before = useEditorStore.getState(), transport = request.getMockImplementation()!
    request.mockImplementation((url: string) => {
      if (kind === 'network') return Promise.reject(new Error('offline'))
      if (kind === 'malformed-draft' && url === '/api/workflows/flow') return Promise.resolve(new Response(JSON.stringify({ ...draft, nodes: null })))
      if (url.includes('/history?') && kind === 'history-failure') return Promise.resolve(new Response('unavailable', { status: 503 }))
      if (url.includes('/history?') && kind === 'missing-active') return Promise.resolve(new Response(JSON.stringify({ workflowId: 'flow', history: [active], active: null })))
      if (url.includes('/history?') && kind === 'history-identity') return Promise.resolve(new Response(JSON.stringify({ workflowId: 'other', history: [], active })))
      if (url.endsWith('/by-deploy/3')) {
        const result = snapshot()
        if (kind === 'snapshot-identity') result.workflowId = 'other'
        if (kind === 'snapshot-number') result.deployNumber = 4
        if (kind === 'snapshot-version') result.version = 99
        if (kind === 'snapshot-structure') result.specJson = JSON.stringify({ content: 'nodes: [' })
        if (kind === 'spec-identity') result.specJson = JSON.stringify({ ...deployed, id: 'other' })
        return Promise.resolve(new Response(JSON.stringify(result)))
      }
      return transport(url)
    })
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
    expect(useEditorStore.getState()).toBe(before)
  })
  it.each([true, false])('maps cached source into the existing hook data shape; draft exists=%s', async exists => {
    if (exists) request.mockResolvedValueOnce(new Response(JSON.stringify(draft)))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
    const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result } = renderHook(() => useEditorWorkflowDocument('flow'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(exists ? draft : deployed)
    expect(result.current.databaseSpec).toEqual(exists ? draft : null)
    expect(result.current.activeDeployNumber).toBe(exists ? null : 3)
    expect(client.getQueryData(['editor-workflow-document', 'flow'])).toEqual({ spec: exists ? draft : deployed, databaseSpec: exists ? draft : null, activeDeployNumber: exists ? null : 3 })
  })
})
