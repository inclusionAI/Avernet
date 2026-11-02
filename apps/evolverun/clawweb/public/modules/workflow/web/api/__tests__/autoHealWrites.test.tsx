import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoHealSave, useAutoHealDeploy } from '../hooks'
import { useAutoHealSave as localSave, useAutoHealDeploy as localDeploy,
  type WorkflowAutoHealSaveRequest, type WorkflowAutoHealDeployRequest } from '../autoHealWrites'

// Real exported hooks and source fetchJson: only the final HTTP boundary is replaced.
const requestId = '123e4567-e89b-42d3-a456-426614174000', commit = 'a'.repeat(40)
const saveInput: WorkflowAutoHealSaveRequest = { workflowId: 'flow', packId: 'pack', diagnosisId: 'diagnosis',
  requestId, createOnly: false, sourceCommit: commit, expectedSavedHead: null, note: '保存修复',
  spec: { id: 'flow', title: 'Workflow', version: '1', nodes: [], input: { large: '中文'.repeat(18000) } } }
const deployInput: WorkflowAutoHealDeployRequest = { workflowId: 'flow', packId: 'pack', diagnosisId: 'diagnosis',
  requestId, candidateCommit: commit, sourceFlowId: 'source-flow', note: '部署', autoRun: false }
const saved = { ok: true, saved: true, workflowId: 'flow', packId: 'pack', requestId, branch: 'saved/flow', commit, version: 1 }
const deployed = { ok: true, deployed: true, workflowId: 'flow', packId: 'pack', requestId, deployNumber: 1, version: 1, tagName: 'deploy/flow/#1', messageId: null }
let queryClient: QueryClient
const fetchMock = vi.fn<typeof fetch>()
const compressor = vi.fn(() => { throw new Error('Unexpected compression') })
function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider> }
function readBlob(blob: Blob): Promise<string> { return new Promise((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob)
}) }
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: 2, retryDelay: 0 } } })
  fetchMock.mockReset().mockImplementation(async url => new Response(JSON.stringify(String(url).endsWith('/save') ? saved : deployed)))
  compressor.mockClear(); vi.stubGlobal('fetch', fetchMock); vi.stubGlobal('CompressionStream', compressor)
})
afterEach(() => { cleanup(); queryClient.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('explicitly overrides both shared star exports', () => {
  expect(useAutoHealSave).toBe(localSave); expect(useAutoHealDeploy).toBe(localDeploy)
})

async function expectTransport(operation: string, input: unknown) {
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, init] = fetchMock.mock.calls[0]
  expect(url).toBe(`/api/auto-heal/${operation}`)
  expect(init).toMatchObject({ method: 'POST', cache: 'no-store' })
  const headers = new Headers(init?.headers)
  expect(headers.get('X-Pack-Version-CSRF')).toBe('1')
  expect(headers.get('Content-Type')).toBe('application/json')
  expect(headers.has('Content-Encoding')).toBe(false)
  expect(init?.body).toBeInstanceOf(Blob)
  expect((init!.body as Blob).type).toBe('application/json')
  expect(JSON.parse(await readBlob(init!.body as Blob))).toEqual(input)
  expect(compressor).not.toHaveBeenCalled()
}

describe('auto-heal write transport', () => {
  it('saves above the gzip threshold with CSRF, all CAS/identity fields, and existing invalidations', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(useAutoHealSave, { wrapper })
    await act(async () => { await expect(result.current.mutateAsync(saveInput)).resolves.toEqual(saved) })
    await expectTransport('save', saveInput)
    expect((fetchMock.mock.calls[0][1]!.body as Blob).size).toBeGreaterThan(14 * 1024)
    expect(invalidate.mock.calls).toEqual([[{ queryKey: ['editor-workflow-document', 'flow'] }], [{ queryKey: ['db-workflows'] }], [{ queryKey: ['workflow-types'] }]])
  })
  it('deploys the exact candidate and requestId including sourceFlowId without dropping nullable legacy fields', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    const input = { ...deployInput, diagnosisId: null }
    await act(async () => { await expect(result.current.mutateAsync(input)).resolves.toEqual(deployed) })
    await expectTransport('deploy', input)
    expect(invalidate.mock.calls).toEqual([[{ queryKey: ['editor-workflow-document', 'flow'] }], [{ queryKey: ['db-workflows'] }], [{ queryKey: ['workflow-types'] }], [{ queryKey: ['runs'] }]])
  })
  it('does not gzip a large deploy note either', async () => {
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    const input = { ...deployInput, note: 'deploy note '.repeat(4000) }
    await act(async () => { await result.current.mutateAsync(input) })
    await expectTransport('deploy', input)
  })
  it('preserves legacy deploy calls without inventing a candidate or operation ID', async () => {
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    const input = { workflowId: 'flow', packId: null, autoRun: false }
    await act(async () => { await result.current.mutateAsync(input) })
    await expectTransport('deploy', input)
  })
  it('preserves save errors without automatically retrying or invalidating success queries', async () => {
    fetchMock.mockImplementation(async () => new Response('{"code":"SAVE_OUTCOME_UNKNOWN"}', { status: 503 }))
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(useAutoHealSave, { wrapper })
    await act(async () => { await expect(result.current.mutateAsync(saveInput)).rejects.toMatchObject({ status: 503 }) })
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(invalidate).not.toHaveBeenCalled()
  })
  it('preserves deploy errors without a hidden second publication', async () => {
    fetchMock.mockRejectedValue(new TypeError('connection lost'))
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    await act(async () => { await expect(result.current.mutateAsync(deployInput)).rejects.toThrow('connection lost') })
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(invalidate).not.toHaveBeenCalled()
  })
})
it('both hook transports are accepted by a real loopback CSRF guard and non-inflating JSON parser', async () => {
  const { default: express } = await import('express')
  const http = await import('node:http')
  const app = express(), received: unknown[] = []
  app.use((req, res, next) => {
    if (req.get('X-Pack-Version-CSRF') !== '1' || req.get('Content-Encoding')) { res.status(403).end(); return }
    next()
  })
  app.use(express.json({ limit: '1mb', inflate: false }))
  app.post('/api/auto-heal/save', (req, res) => { received.push(req.body); res.json(saved) })
  app.post('/api/auto-heal/deploy', (req, res) => { received.push(req.body); res.json(deployed) })
  const server = await new Promise<import('node:http').Server>(resolve => { const running = app.listen(0, '127.0.0.1', () => resolve(running)) })
  try {
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('No test port')
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      const body = await readBlob(init.body as Blob)
      return new Promise((resolve, reject) => {
        const request = http.request({ host: '127.0.0.1', port: address.port, path: url, method: init.method, headers: init.headers as Record<string, string> }, response => {
          let data = ''
          response.setEncoding('utf8'); response.on('data', chunk => { data += chunk })
          response.on('end', () => resolve(new Response(data, { status: response.statusCode })))
        })
        request.on('error', reject); request.end(body)
      })
    })
    const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
    await act(async () => {
      await expect(result.current.save.mutateAsync(saveInput)).resolves.toEqual(saved)
      await expect(result.current.deploy.mutateAsync(deployInput)).resolves.toEqual(deployed)
    })
    expect(received).toEqual([saveInput, deployInput]); expect(compressor).not.toHaveBeenCalled()
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())) }
})
