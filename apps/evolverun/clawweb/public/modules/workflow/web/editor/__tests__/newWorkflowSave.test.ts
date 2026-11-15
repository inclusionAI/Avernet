import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readDatabaseWorkflowDraft, originalDraftPackId } from '../../api/editorWorkflowSource'
import { sessionFor } from '../packVersionSession'
import { createWorkflowPack, CREATE_WORKFLOW_TIMEOUT_MS } from '../newWorkflowSave'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
vi.mock('../../api/editorWorkflowSource', () => ({ readDatabaseWorkflowDraft: vi.fn(), originalDraftPackId: vi.fn((value: { id: string } | null) => value?.id) }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
const B = 'b'.repeat(40), C = 'c'.repeat(40)
let seq = 0
const spec = () => ({ id: `safe-new-${++seq}`, version: '1', title: 'New', nodes: [] })
const readBlob = (blob: Blob): Promise<string> => new Promise(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function saved(id: string) { return { ok: true, saved: true, workflowId: id, packId: id, branch: `wf/${id}`, commit: B } }
function snapshot(value: ReturnType<typeof spec>, commit = B) {
  return { workflowId: value.id, packId: value.id, baseRef: `origin/wf/${value.id}`, baseCommit: commit, draftHead: commit,
    versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: value.id, workflows: [value.id] })), mode: '100644' },
      { path: `workflows/${value.id}.yaml`, contentBase64: btoa(stringify(value)), mode: '100644' }] }
}
it('creates only once and verifies the acknowledged 保存版本 commit and DB content', async () => {
  const value = spec(); vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(value))
  vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce(value)
  const result = await createWorkflowPack(value)
  const [url, init] = vi.mocked(fetchJson).mock.calls[0]
  expect(url).toBe('/api/auto-heal/save')
  expect(init?.headers).toEqual({ 'X-Pack-Version-CSRF': '1' })
  expect(JSON.parse(await readBlob(init!.body as Blob))).toMatchObject({ workflowId: value.id, packId: value.id, spec: value, createOnly: true, expectedSavedHead: null, requestId: expect.any(String) })
  expect(result.result.commit).toBe(B); expect(result.snapshot.baseCommit).toBe(B)
  expect(sessionFor(value.id).state.creation?.phase).toBe('completed')
  await expect(createWorkflowPack(value)).rejects.toThrow('请勿重复创建')
  expect(fetchJson).toHaveBeenCalledTimes(2)
})
it.each([
  ['network', new TypeError('lost')],
  ['unknown server error', { status: 503, body: '{"code":"CREATE_OUTCOME_UNKNOWN"}' }],
  ['generic 409', { status: 409, body: 'bad gateway' }],
  ['generic 500', { status: 500, body: '{"code":"SAVE_FAILED"}' }],
])('fails closed on %s and never retries or queries a journal', async (_name, failure) => {
  const value = spec(); vi.mocked(fetchJson).mockRejectedValueOnce(failure)
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  await expect(createWorkflowPack({ ...value, title: 'Later' })).rejects.toThrow('勿重试创建')
  expect(sessionFor(value.id).state.creation?.input.spec).toEqual(value)
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it.each([
  ['CREATE_DB_OUTCOME_UNKNOWN', '数据库提交结果未知；Git 推送未启动。'],
  ['CREATE_DB_CONFIRMED_GIT_NOT_STARTED', '数据库创建已提交，但提交后复核未通过或无法完成；Git 推送未启动。'],
  ['CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', '数据库创建已提交；Git 推送是否开始或成功均未知。'],
])('explains %s without retrying or exposing error payloads', async (code, detail) => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 503, body: JSON.stringify({ code, branch: `wf/${value.id}`, commit: B, message: 'secret-value' }) })
  await expect(createWorkflowPack(value)).rejects.toThrow(detail)
  const error = sessionFor(value.id).state.creation!.error!
  expect(error).toContain('勿重试创建'); expect(error).not.toContain('secret-value')
  if (code === 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN') {
    expect(error).toContain(`拟更新的分支：wf/${value.id}`)
    expect(error).toContain('这些信息不证明 Git 推送已开始或成功')
    expect(error).not.toContain('尝试推送')
  } else {
    // A malformed receipt cannot claim a push that the phase code says never started.
    expect(error).not.toContain('拟更新的分支')
  }
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not imply Git push started when helper validation failed before the command', async () => {
  const value = spec(), branch = `wf/${value.id}`
  // This is the receipt returned by the service for a pre-push helper failure.
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 503, body: JSON.stringify({
    code: 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', branch, commit: B,
  }) })
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  const message = sessionFor(value.id).state.creation!.error!
  expect(message).toContain(`拟更新的分支：${branch}`)
  expect(message).toContain(`候选 commit：${B}`)
  expect(message).toContain('不证明 Git 推送已开始或成功')
  expect(message).not.toContain('尝试推送')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not trust phase codes from the wrong HTTP status', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 500, body: JSON.stringify({ code: 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED' }) })
  await expect(createWorkflowPack(value)).rejects.toThrow('数据库或 Git 可能已变更')
  expect(sessionFor(value.id).state.creation!.error).not.toContain('数据库创建已提交')
})
it('recognizes only the explicit conflict and never resubmits', async () => {
  const value = spec(); vi.mocked(fetchJson).mockRejectedValueOnce({ status: 409, body: '{"code":"WORKFLOW_ALREADY_EXISTS"}' })
  await expect(createWorkflowPack(value)).rejects.toThrow('未覆盖')
  await expect(createWorkflowPack(value)).rejects.toThrow('更换 ID')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('allows a corrected retry only after an explicit pre-write rejection', async () => {
  const value = spec(); vi.mocked(fetchJson).mockRejectedValueOnce({ status: 400, body: '{"code":"INVALID_WORKFLOW"}' })
  await expect(createWorkflowPack(value)).rejects.toThrow('未执行创建')
  const original = sessionFor(value.id).state.creation!.input.requestId
  const corrected = { ...value, title: 'Corrected' }
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(corrected))
  vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce(corrected)
  await createWorkflowPack(corrected)
  expect(sessionFor(value.id).state.creation!.input.requestId).not.toBe(original)
})
it.each(['missing commit', 'wrong branch', 'wrong identity', 'invalid commit'])('rejects a malformed acknowledgement: %s', async kind => {
  const value = spec(), response: Record<string, unknown> = saved(value.id)
  if (kind === 'missing commit') delete response.commit
  if (kind === 'wrong branch') response.branch = 'wf/other'
  if (kind === 'wrong identity') response.workflowId = 'other'
  if (kind === 'invalid commit') response.commit = 'not-a-commit'
  vi.mocked(fetchJson).mockResolvedValueOnce(response)
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not adopt a moved head and only rereads after the acknowledged create', async () => {
  const value = spec(); vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(value, C))
  await expect(createWorkflowPack(value)).rejects.toThrow('保存版本已移动')
  expect(sessionFor(value.id).state.creation?.phase).toBe('saved')
  vi.mocked(fetchJson).mockResolvedValueOnce(snapshot(value)); vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce(value)
  await createWorkflowPack(value)
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('keeps the draft if DB content differs and never resends the create', async () => {
  const value = spec(); vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(value))
  vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce({ ...value, title: 'Other' })
  await expect(createWorkflowPack(value)).rejects.toThrow('数据库内容与提交草稿不一致')
  expect(sessionFor(value.id).state.creation?.phase).toBe('saved')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
})
it('times out and ignores late acknowledgements without retrying', async () => {
  const value = spec(); let resolve!: (value: unknown) => void
  const late = new Promise(r => { resolve = r }); vi.useFakeTimers(); vi.mocked(fetchJson).mockReturnValueOnce(late)
  const pending = expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  await vi.advanceTimersByTimeAsync(CREATE_WORKFLOW_TIMEOUT_MS); await pending
  resolve(saved(value.id)); await late; await Promise.resolve()
  expect(sessionFor(value.id).state.creation?.phase).toBe('unknown')
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not overwrite version file edits made while verifying creation', async () => {
  const value = spec(); let resolve!: (value: unknown) => void
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockReturnValueOnce(new Promise(r => { resolve = r }))
  const pending = expect(createWorkflowPack(value)).rejects.toThrow('版本文件状态已变化')
  await vi.waitFor(() => expect(fetchJson).toHaveBeenCalledTimes(2))
  const session = sessionFor(value.id)
  const { publish } = await import('../packVersionSession')
  publish(session, { ...session.state, versions: { ...session.state.versions, draft: { ...session.state.versions.draft, blocked: true } } })
  vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce(value); resolve(snapshot(value)); await pending
  expect(session.state.versions.draft.blocked).toBe(true)
  expect(session.state.creation?.phase).toBe('saved')
  await expect(createWorkflowPack(value)).rejects.toThrow('已有版本文件草稿')
  expect(fetchJson).toHaveBeenCalledTimes(2)
})

it.each([40, 64])('shows validated %i-character candidate identity without implying push started or succeeded or retrying', async length => {
  const value = spec(), commit = 'a'.repeat(length), branch = `wf/${value.id}`
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 503, body: JSON.stringify({ code: 'CREATE_OUTCOME_UNKNOWN', branch, commit, detail: 'do-not-display' }) })
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  const creation = sessionFor(value.id).state.creation!
  expect(creation.phase).toBe('unknown')
  expect(creation.error).toContain(`拟更新的分支：${branch}`)
  expect(creation.error).toContain(`候选 commit：${commit}`)
  expect(creation.error).toContain('不证明 Git 推送已开始或成功')
  expect(creation.error).not.toContain('do-not-display')
  await expect(createWorkflowPack(value)).rejects.toThrow(branch)
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it.each([
  ['wrong status', 500, 'CREATE_OUTCOME_UNKNOWN', 'valid', 'valid'],
  ['wrong code', 503, 'OTHER', 'valid', 'valid'],
  ['different branch', 503, 'CREATE_OUTCOME_UNKNOWN', 'wf/secret-value', 'valid'],
  ['branch newline', 503, 'CREATE_OUTCOME_UNKNOWN', 'newline', 'valid'],
  ['invalid commit', 503, 'CREATE_OUTCOME_UNKNOWN', 'valid', 'secret-value'],
  ['missing commit', 503, 'CREATE_OUTCOME_UNKNOWN', 'valid', undefined],
  ['uppercase commit', 503, 'CREATE_OUTCOME_UNKNOWN', 'valid', 'A'.repeat(40)],
  ['commit with newline', 503, 'CREATE_OUTCOME_UNKNOWN', 'valid', 'a'.repeat(40) + '\nsecret-value'],
  ['commit with trailing newline', 503, 'CREATE_OUTCOME_UNKNOWN', 'valid', 'a'.repeat(40) + '\n'],
])('does not expose untrusted identity for %s', async (_label, status, code, branchValue, commitValue) => {
  const value = spec(), branch = branchValue === 'valid' ? `wf/${value.id}` : branchValue === 'newline' ? `wf/${value.id}\nsecret-value` : branchValue
  const commit = commitValue === 'valid' ? B : commitValue
  vi.mocked(fetchJson).mockRejectedValueOnce({ status, body: JSON.stringify({ code, branch, commit, message: 'secret-value' }) })
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  const message = sessionFor(value.id).state.creation!.error!
  expect(message).not.toContain('secret-value')
  expect(message).not.toContain('拟更新的分支')
  expect(message).not.toContain(B)
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not expose raw API errors after an acknowledged create', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockRejectedValueOnce(new Error('API 503: secret-value'))
  await expect(createWorkflowPack(value)).rejects.toThrow('保存版本快照尚未校验')
  expect(sessionFor(value.id).state.creation!.error).not.toContain('secret-value')
  expect(sessionFor(value.id).state.creation!.phase).toBe('saved')
})

it('does not display an candidate branch containing an unsafe workflow ID', async () => {
  const value = { ...spec(), id: 'bad-id\n' }
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 503, body: JSON.stringify({ code: 'CREATE_OUTCOME_UNKNOWN', branch: `wf/${value.id}`, commit: B }) })
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  expect(sessionFor(value.id).state.creation!.error).not.toContain('拟更新的分支')
})
it('does not leak an obsolete request error', async () => {
  const value = spec(); let reject!: (cause: unknown) => void
  vi.mocked(fetchJson).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
  const pending = createWorkflowPack(value)
  sessionFor(value.id).createRequest = {}
  reject(new Error('secret-value'))
  await expect(pending).rejects.toThrow('新建响应已过期')
  await expect(pending).rejects.not.toThrow('secret-value')
})
it.each([null, 'different-pack'])('does not adopt a mismatched raw DB pack_id %s after create', async packId => {
  const value = spec()
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(value))
  vi.mocked(readDatabaseWorkflowDraft).mockResolvedValueOnce(value)
  vi.mocked(originalDraftPackId).mockReturnValueOnce(packId)
  await expect(createWorkflowPack(value)).rejects.toThrow('数据库 Pack 身份与创建确认不一致')
  expect(sessionFor(value.id).state.creation?.phase).toBe('saved')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('ignores a late DB read after the verification timeout without marking creation complete', async () => {
  const value = spec(); let resolve!: (value: typeof value) => void
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce(snapshot(value))
  vi.mocked(readDatabaseWorkflowDraft).mockReturnValueOnce(new Promise(r => { resolve = r }))
  vi.useFakeTimers()
  const pending = expect(createWorkflowPack(value)).rejects.toThrow('保存版本快照尚未校验')
  await vi.waitFor(() => expect(readDatabaseWorkflowDraft).toHaveBeenCalled())
  await vi.advanceTimersByTimeAsync(CREATE_WORKFLOW_TIMEOUT_MS); await pending
  resolve(value); await Promise.resolve(); await Promise.resolve()
  expect(sessionFor(value.id).state.creation?.phase).toBe('saved')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
})

it('does not accept a legacy branch acknowledgement as a wf create receipt', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockResolvedValueOnce({ ...saved(value.id), branch: `saved/${value.id}` })
  await expect(createWorkflowPack(value)).rejects.toThrow('勿重试创建')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('does not confirm a wf create using a same-commit legacy fallback snapshot', async () => {
  const value = spec()
  vi.mocked(fetchJson).mockResolvedValueOnce(saved(value.id)).mockResolvedValueOnce({ ...snapshot(value), baseRef: `origin/saved/${value.id}` })
  await expect(createWorkflowPack(value)).rejects.toThrow('保存版本快照尚未校验')
  expect(sessionFor(value.id).state.creation?.phase).toBe('saved')
  expect(sessionFor(value.id).state.versions.draft.snapshot).toBeNull()
  expect(readDatabaseWorkflowDraft).not.toHaveBeenCalled()
})
