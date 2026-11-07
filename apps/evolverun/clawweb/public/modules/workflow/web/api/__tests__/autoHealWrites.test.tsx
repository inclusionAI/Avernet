import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoHealSave, useAutoHealDeploy } from '../hooks'
import { useAutoHealSave as localSave, useAutoHealDeploy as localDeploy,
  autoHealSaveIsCurrent, bindAutoHealDeployToSave, bindEditorDeploymentWriteEpoch, isAutoHealWriteNotSent, isDeployPrePushConflict, PostSaveLockError, type WorkflowAutoHealSaveRequest, type WorkflowAutoHealDeployRequest } from '../autoHealWrites'
import { assertEditorWorkflowReadable, editorWorkflowWriteState } from '../../editor/workflowWriteGuard'
import { publish, releaseCleanSession, sessionFor } from '../../editor/packVersionSession'

// Real exported hooks and source fetchJson: only the final HTTP boundary is replaced.
const requestId = '123e4567-e89b-42d3-a456-426614174000', commit = 'a'.repeat(40)
const saveInput: WorkflowAutoHealSaveRequest = { workflowId: 'flow', packId: 'pack', diagnosisId: 'diagnosis',
  requestId, createOnly: false, sourceCommit: commit, expectedSavedHead: null, note: '保存修复',
  spec: { id: 'flow', title: 'Workflow', version: '1', nodes: [], input: { large: '中文'.repeat(18000) } } }
const deployInput: WorkflowAutoHealDeployRequest = { workflowId: 'flow', packId: 'pack', diagnosisId: 'diagnosis',
  requestId, candidateCommit: commit, sourceFlowId: 'source-flow', note: '部署', autoRun: false }
const saved = { ok: true, saved: true, workflowId: 'flow', packId: 'pack', requestId, branch: 'wf/flow', commit, version: 1 }
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
  bindEditorDeploymentWriteEpoch(deployInput, assertEditorWorkflowReadable(deployInput.workflowId))
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
  it.each([40, 64])('confirms an ordinary save only with its wf branch and a %i-character OID', async length => {
    const workflowId = `ordinary-valid-${length}`
    const receipt = { ...saved, workflowId, branch: `wf/${workflowId}`, commit: 'a'.repeat(length) }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(receipt)))
    const { result } = renderHook(useAutoHealSave, { wrapper })
    let confirmed: Awaited<ReturnType<typeof result.current.mutateAsync>>
    await act(async () => { confirmed = await result.current.mutateAsync({ ...saveInput, workflowId }) })
    expect(confirmed!).toEqual(receipt)
    expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
    expect(autoHealSaveIsCurrent(confirmed!)).toBe(true)
    expect(() => bindAutoHealDeployToSave({ ...deployInput, workflowId }, confirmed!)).not.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it.each([
    ['missing-branch', { branch: undefined }],
    ['null-branch', { branch: null }],
    ['non-string-branch', { branch: 1 }],
    ['legacy-branch', { branch: 'saved/flow' }],
    ['remote-alias-branch', { branch: 'origin/wf/flow' }],
    ['other-workflow-branch', { branch: 'wf/other' }],
    ['missing-commit', { commit: undefined }],
    ['null-commit', { commit: null }],
    ['non-string-commit', { commit: 1 }],
    ['malformed-commit', { commit: 'not-an-oid' }],
    ['short-sha1', { commit: 'a'.repeat(39) }],
    ['long-sha1', { commit: 'a'.repeat(41) }],
    ['short-sha256', { commit: 'a'.repeat(63) }],
    ['long-sha256', { commit: 'a'.repeat(65) }],
    ['uppercase-commit', { commit: 'A'.repeat(40) }],
    ['non-hex-commit', { commit: 'g'.repeat(40) }],
    ['sha1-trailing-newline', { commit: 'a'.repeat(40) + '\n' }],
    ['sha256-trailing-newline', { commit: 'a'.repeat(64) + '\n' }],
  ])('keeps ordinary save %s uncertain without granting a deployment baseline', async (name, fields) => {
    const workflowId = `ordinary-invalid-${name}`
    const body = JSON.stringify({ ...saved, workflowId, branch: `wf/${workflowId}`, ...fields })
    fetchMock.mockResolvedValueOnce(new Response(body))
    const parsed = vi.spyOn(JSON, 'parse')
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries'), onSuccess = vi.fn()
    const { result } = renderHook(useAutoHealSave, { wrapper })
    const input = { ...saveInput, workflowId }
    let failure: unknown
    await act(async () => {
      try { await result.current.mutateAsync(input, { onSuccess }) } catch (error) { failure = error }
    })
    expect(failure).toBeInstanceOf(Error)
    expect((failure as Error).message).toContain('写入结果未知')
    expect(isAutoHealWriteNotSent(failure, input)).toBe(false)
    expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
    expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${encodeURIComponent(workflowId)}`)).not.toBeNull()
    expect(() => assertEditorWorkflowReadable(workflowId)).toThrow('需人工核对')
    // Check the actual object returned by JSON.parse, not a lookalike: baseline
    // capabilities are keyed by receipt identity, independently of the lock.
    const index = parsed.mock.calls.findIndex(([text]) => text === body)
    expect(index).toBeGreaterThanOrEqual(0)
    const receipt = parsed.mock.results[index].value
    expect(autoHealSaveIsCurrent(receipt)).toBe(false)
    expect(() => bindAutoHealDeployToSave({ ...deployInput, workflowId }, receipt)).toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(invalidate).not.toHaveBeenCalled()
    expect(onSuccess).not.toHaveBeenCalled()
  })
  it('deploys the exact candidate and requestId including sourceFlowId without dropping nullable legacy fields', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    const input = { ...deployInput, diagnosisId: null }
    await act(async () => { await expect(result.current.mutateAsync(input)).resolves.toEqual(deployed) })
    await expectTransport('deploy', input)
    expect(invalidate.mock.calls).toEqual([[{ queryKey: ['editor-workflow-document', 'flow'] }], [{ queryKey: ['db-workflows'] }], [{ queryKey: ['workflow-types'] }], [{ queryKey: ['runs'] }]])
  })
  it('routes editor deployment through the separately guarded endpoint without changing AutoHeal defaults', async () => {
    const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
    const input = { ...deployInput, expectedDraftToken: 'b'.repeat(64) }
    bindEditorDeploymentWriteEpoch(input, assertEditorWorkflowReadable(input.workflowId))
    await act(async () => { await expect(result.current.mutateAsync(input)).resolves.toEqual(deployed) })
    await expectTransport('editor-deploy', input)
  })
  it('sends snapshot-v2 only to the versioned Editor endpoint', async () => {
    const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
    const input = { ...deployInput, expectedDraftToken: 'b'.repeat(64), snapshotProtocol: 'saved-tree-v2' as const }
    bindEditorDeploymentWriteEpoch(input, assertEditorWorkflowReadable(input.workflowId))
    await act(async () => { await result.current.mutateAsync(input) })
    await expectTransport('editor-deploy/snapshot-v2', input)
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
    await act(async () => { await expect(result.current.mutateAsync({ ...saveInput, workflowId: 'save-error-flow' })).rejects.toMatchObject({ status: 503 }) })
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(invalidate).not.toHaveBeenCalled()
  })
  it('explains deployment tree conflicts without exposing paths or retrying', async () => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ code: 'DEPLOY_TREE_CONFLICT', requestId }), { status: 409 }))
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    await act(async () => {
      await expect(result.current.mutateAsync(deployInput)).rejects.toMatchObject({
        status: 409, message: expect.stringContaining('当前生效 tag 缺失'),
      })
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it.each([
    [503, '{"code":"DEPLOY_TREE_CONFLICT"}'],
    [409, '{"code":"DEPLOY_TREE_CONFLICT"}'],
    [409, '{"code":"OTHER"}'],
    [409, '{"code":"DEPLOY_TREE_CONFLICT","requestId":"other-request"}'],
    [409, 'invalid json'],
  ])('keeps status %s with body %s uncertain', async (status, body) => {
    fetchMock.mockResolvedValueOnce(new Response(body, { status }))
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    let failure: unknown
    await act(async () => { try { await result.current.mutateAsync(deployInput) } catch (error) { failure = error } })
    expect(isDeployPrePushConflict(failure, deployInput)).toBe(false)
  })
  it('binds a verified conflict to its exact workflow and request', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'DEPLOY_TREE_CONFLICT', requestId }), { status: 409 }))
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    let failure: unknown
    await act(async () => { try { await result.current.mutateAsync(deployInput) } catch (error) { failure = error } })
    expect(isDeployPrePushConflict(failure, deployInput)).toBe(true)
    expect(isDeployPrePushConflict(failure, { workflowId: 'other', requestId })).toBe(false)
    expect(isDeployPrePushConflict(failure, { workflowId: 'flow', requestId: 'other' })).toBe(false)
  })
  it('recognizes a verified missing deployment baseline as a pre-push rejection', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'DEPLOY_BASELINE_MISSING', requestId }), { status: 409 }))
    const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
    let failure: unknown
    await act(async () => { try { await result.current.mutateAsync(deployInput) } catch (error) { failure = error } })
    expect(isDeployPrePushConflict(failure, deployInput)).toBe(true)
    expect(isDeployPrePushConflict(failure, { workflowId: 'other', requestId })).toBe(false)
  })
  it.each([true, false])('recognizes a draft conflict only for the editor route (editor=%s)', async editorGuard => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'DRAFT_CONFLICT', requestId }), { status: 409 }))
    const { result } = renderHook(() => useAutoHealDeploy({ editorGuard }), { wrapper })
    let failure: unknown
    await act(async () => { try { await result.current.mutateAsync(deployInput) } catch (error) { failure = error } })
    expect(isDeployPrePushConflict(failure, deployInput)).toBe(editorGuard)
  })
  it('does not classify a failed body read even if statusText resembles a verified rejection', async () => {
    const stream = new ReadableStream({ start(controller) { controller.error(new Error('body lost')) } })
    fetchMock.mockResolvedValueOnce(new Response(stream, { status: 409, statusText: JSON.stringify({ code: 'DEPLOY_BASELINE_MISSING', requestId }) }))
    const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
    let failure: unknown
    await act(async () => { try { await result.current.mutateAsync(deployInput) } catch (error) { failure = error } })
    expect(isDeployPrePushConflict(failure, deployInput)).toBe(false)
  })
  it('does not classify an arbitrary thrown error with matching status and body as a server conflict', async () => {
    const fake = Object.assign(new Error('network error'), { status: 409, body: '{"code":"DEPLOY_TREE_CONFLICT"}' })
    fetchMock.mockRejectedValueOnce(fake)
    const { result } = renderHook(useAutoHealDeploy, { wrapper })
    await act(async () => { await expect(result.current.mutateAsync(deployInput)).rejects.toBe(fake) })
    expect(fake.message).toBe('network error')
  })
  it.each(['save', 'deploy'] as const)('rejects locked %s at the real transport boundary without sending', async operation => {
    const session = sessionFor('flow')
    publish(session, { ...session.state, postSaveVerificationError: 'postflight failed' })
    try {
      const hook = renderHook(() => {
        const save = useAutoHealSave(), deploy = useAutoHealDeploy()
        return () => operation === 'save' ? save.mutateAsync(saveInput) : deploy.mutateAsync(deployInput)
      }, { wrapper })
      await act(async () => { await expect(hook.result.current()).rejects.toBeInstanceOf(PostSaveLockError) })
      expect(fetchMock).not.toHaveBeenCalled()
      expect(Object.hasOwn(operation === 'save' ? saveInput : deployInput, 'toJSON')).toBe(false)
    } finally {
      publish(session, { ...session.state, postSaveVerificationError: null })
      releaseCleanSession('flow', session)
    }
  })
  it('does not change the separate editor-deploy transport contract', async () => {
    const session = sessionFor('flow')
    publish(session, { ...session.state, postSaveVerificationError: 'postflight failed' })
    try {
      const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
      await act(async () => { await expect(result.current.mutateAsync(deployInput)).resolves.toEqual(deployed) })
      await expectTransport('editor-deploy', deployInput)
    } finally {
      publish(session, { ...session.state, postSaveVerificationError: null })
      releaseCleanSession('flow', session)
    }
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
