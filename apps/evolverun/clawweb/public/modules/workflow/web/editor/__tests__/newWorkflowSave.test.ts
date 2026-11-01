import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { sessionFor } from '../packVersionSession'
import { createWorkflowPack, reconcileNewWorkflow, CREATE_WORKFLOW_TIMEOUT_MS } from '../newWorkflowSave'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
const B = 'b'.repeat(40), C = 'c'.repeat(40)
let seq = 0
const spec = () => ({ id: `creation-${++seq}`, version: '1', title: 'New', nodes: [] })
const readBlob = (blob: Blob): Promise<string> => new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
function saved(workflowId: string, requestId: string) { return { branch: `saved/${workflowId}`, commit: B, requestId, version: 1, packId: workflowId } }
function snapshot(value: ReturnType<typeof spec>, commit = B) {
  return { workflowId: value.id, packId: value.id, canWrite: true, baseRef: `origin/saved/${value.id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: value.id, workflows: [value.id] })), mode: '100644' },
      { path: `workflows/${value.id}.yaml`, contentBase64: btoa(stringify(value)), mode: '100644' }] }
}
it('atomically creates via createOnly and seeds the exact saved snapshot without a legacy DB write', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/save') {
      const input = JSON.parse(await readBlob(init!.body as Blob))
      expect(input).toMatchObject({ workflowId: value.id, packId: value.id, spec: value, createOnly: true, expectedSavedHead: null, requestId: expect.stringMatching(/^[\da-f-]{36}$/) })
      expect(input).not.toHaveProperty('sourceCommit')
      return saved(value.id, input.requestId)
    }
    expect(url).toBe(`/api/workflows/${value.id}/pack-version?version=draft`)
    return snapshot(value)
  })
  const result = await createWorkflowPack(value)
  expect(result.snapshot.baseCommit).toBe(B)
  expect(sessionFor(value.id).state.creation!.phase).toBe('completed')
  expect(sessionFor(value.id).state.versions.draft.snapshot!.baseCommit).toBe(B)
  expect(fetchJson).toHaveBeenCalledTimes(2)
  expect(vi.mocked(fetchJson).mock.calls.some(([url]) => url === '/api/workflows/save')).toBe(false)
})
it('fails on duplicate ID with no upsert fallback or automatic new UUID', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 409, body: '{"code":"WORKFLOW_ALREADY_EXISTS"}' })
  await expect(createWorkflowPack(value)).rejects.toThrow('未覆盖原工作流')
  const attempt = sessionFor(value.id).state.creation!
  await expect(createWorkflowPack({ ...value, title: 'Updated' })).rejects.toThrow('请更换新 ID')
  expect(fetchJson).toHaveBeenCalledTimes(1)
  expect(sessionFor(value.id).state.creation!.input.requestId).toBe(attempt.input.requestId)
  expect(attempt.input.spec).toEqual(value)
})
it('retries an unknown creation with exactly the original bytes and requestId even if the user kept editing', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await expect(createWorkflowPack(value)).rejects.toThrow('数据库可能已创建而 Git 待保存')
  const attempt = sessionFor(value.id).state.creation!
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id, attempt.input.requestId)).mockResolvedValueOnce(snapshot(value))
  const created = await createWorkflowPack({ ...value, title: 'Later local edit' })
  expect(created.requestedSpec.title).toBe('New')
  expect(await readBlob(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).toBe(await readBlob(vi.mocked(fetchJson).mock.calls[1][1]!.body as Blob))
})
it('does not claim a pre-push failure is a saved workflow, and retains the draft for recovery', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await expect(createWorkflowPack(value)).rejects.toThrow()
  const attempt = sessionFor(value.id).state.creation!
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: attempt.input.requestId, kind: 'save', state: 'failed' })
  await expect(reconcileNewWorkflow(value.id)).rejects.toThrow('数据库工作流已创建，Git 初始化在推送前失败')
  expect(sessionFor(value.id).state.creation!.phase).toBe('failed')
  expect(sessionFor(value.id).state.creation!.input).toEqual(attempt.input)
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
  expect(vi.mocked(fetchJson).mock.calls[1][1]).not.toHaveProperty('method')
})
it('recovers via explicit POST and only then loads the confirmed commit', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await expect(createWorkflowPack(value)).rejects.toThrow()
  const requestId = sessionFor(value.id).state.creation!.input.requestId
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId, kind: 'save', state: 'completed', result: saved(value.id, requestId) }).mockResolvedValueOnce(snapshot(value))
  await reconcileNewWorkflow(value.id, true)
  expect(vi.mocked(fetchJson).mock.calls[1]).toEqual([`/api/workflows/${value.id}/pack-operations/${requestId}/reconcile`, expect.objectContaining({ method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' } })])
  expect(sessionFor(value.id).state.creation!.phase).toBe('completed')
})
it('never adopts a newer saved head; snapshot retry cannot reissue the create operation', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockImplementationOnce(async (_url, init) => saved(value.id, JSON.parse(await readBlob(init!.body as Blob)).requestId)).mockResolvedValueOnce(snapshot(value, C))
  await expect(createWorkflowPack(value)).rejects.toThrow('不能替换原始保存基线')
  expect(sessionFor(value.id).state.creation!.phase).toBe('saved')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
  vi.mocked(fetchJson).mockResolvedValueOnce(snapshot(value))
  await createWorkflowPack(value)
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('bounds an unresponsive creation and ignores its late result without reading a new baseline', async () => {
  const value = spec()
  let resolve!: (result: unknown) => void
  const late = new Promise(r => { resolve = r })
  vi.useFakeTimers(); vi.mocked(fetchJson).mockReturnValueOnce(late)
  const pending = expect(createWorkflowPack(value)).rejects.toThrow('新建保存结果尚未确认')
  const requestId = sessionFor(value.id).state.creation!.input.requestId
  await vi.advanceTimersByTimeAsync(CREATE_WORKFLOW_TIMEOUT_MS); await pending
  resolve(saved(value.id, requestId)); await late
  expect(sessionFor(value.id).state.creation!.phase).toBe('unknown')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('resumes a confirmed pre-push failure with the same createOnly request, not a new DB upsert or UUID', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await expect(createWorkflowPack(value)).rejects.toThrow()
  const attempt = sessionFor(value.id).state.creation!
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: attempt.input.requestId, kind: 'save', state: 'failed' })
  await expect(reconcileNewWorkflow(value.id)).rejects.toThrow('推送前失败')
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id, attempt.input.requestId)).mockResolvedValueOnce(snapshot(value))
  await createWorkflowPack(value)
  expect(await readBlob(vi.mocked(fetchJson).mock.calls[2][1]!.body as Blob)).toEqual(await readBlob(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob))
  expect(sessionFor(value.id).state.creation!.phase).toBe('completed')
})
it('preserves a confirmed Git result when snapshot read returns a duplicate-like conflict and retries only the snapshot', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockImplementationOnce(async (_url, init) => saved(value.id, JSON.parse(await readBlob(init!.body as Blob)).requestId))
    .mockRejectedValueOnce({ status: 409, body: '{"code":"WORKFLOW_ALREADY_EXISTS"}' })
  await expect(createWorkflowPack(value)).rejects.toThrow('新建已保存到 Git')
  expect(sessionFor(value.id).state.creation!.phase).toBe('saved')
  vi.mocked(fetchJson).mockResolvedValueOnce(snapshot(value))
  await reconcileNewWorkflow(value.id, true)
  expect(sessionFor(value.id).state.creation!.phase).toBe('completed')
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
