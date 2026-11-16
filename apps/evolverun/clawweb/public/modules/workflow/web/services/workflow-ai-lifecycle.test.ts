import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createHash, webcrypto } from 'node:crypto'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { stringify } from 'yaml'
const api = vi.hoisted(() => ({ capabilities: vi.fn(), prepare: vi.fn(), generate: vi.fn(), poll: vi.fn(), cancel: vi.fn(), validate: vi.fn() }))
vi.mock('../api/workflow-ai', () => ({ workflowAiApi: api }))
import { generateWorkflowAiDraft, WorkflowAiCandidateError } from './workflow-ai-draft'
import { createWorkflowAiAttempt, WorkflowAiRecoveryError } from './workflow-ai-lifecycle'
import { workflowAiYaml } from './workflow-ai-spec'

const source: WorkflowSpec = { id: 'workflow', version: '1', title: 'Source', nodes: [{ id: 'n', title: 'Node', executor: { type: 'done' }, _x: 1, _y: 2 }] }
const digest = (text: string) => createHash('sha256').update(text).digest('hex')
const task = (status: string, extra: object = {}) => ({ draftId: 'draft', workflowId: source.id, inputSpecDigest: digest(workflowAiYaml(source)), status, ...extra })
function completed() { return task('completed', { result: { yaml: stringify(source), summary: 'Draft' } }) }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
beforeEach(() => {
  vi.useFakeTimers(); Object.values(api).forEach(mock => mock.mockReset())
  vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID(), subtle: { digest: async (_algorithm, bytes) => createHash('sha256').update(bytes).digest().buffer } })
  api.capabilities.mockResolvedValue({ available: true })
  api.prepare.mockImplementation(async (_id, input) => ({ draftId: 'draft', receipt: 'signed-receipt', inputSpecDigest: digest(input.currentYaml) }))
  api.generate.mockResolvedValue({ draftId: 'draft', status: 'pending' })
  api.poll.mockResolvedValue(completed())
  api.cancel.mockResolvedValue(task('cancelled'))
  api.validate.mockImplementation(async spec => ({ valid: true, issues: [], normalizedSpec: spec }))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('AutoHeal/BaaS draft lifecycle safety', () => {
  it('passes raw requirements and captures an immutable applied source before awaiting capabilities', async () => {
    const check = deferred<{ available: boolean }>()
    api.capabilities.mockReturnValueOnce(check.promise)
    const current = structuredClone(source)
    const promise = generateWorkflowAiDraft('create', ' \n原始需求  ', current)
    current.title = 'edited after submission'
    check.resolve({ available: true })
    const result = await promise
    const input = api.generate.mock.calls[0][1]
    expect(input.prompt).toBe(' \n原始需求  ')
    expect(input.mode).toBe('create')
    expect(input.currentYaml).toBe(workflowAiYaml(source))
    expect(result.spec.title).toBe('Source')
    expect(api.capabilities).toHaveBeenCalledBefore(api.generate)
  })

  it.each([
    'AUTO_HEAL_BOT_UNAVAILABLE',
    undefined,
  ])('fails closed when the server reports unavailable configuration: %s', async reason => {
    api.capabilities.mockResolvedValueOnce(reason === undefined ? { available: false } : { available: false, reason })
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause as Error)) as Error
    expect(failure.message).toContain('AI 草稿服务不可用')
    expect(failure.message).toContain('autoHeal.botId')
    expect(failure.message).toContain('BaaS')
    expect(failure.message).toContain('浏览器不会选择或覆盖 Bot')
    if (reason) expect(failure.message).toContain(reason)
    else expect(failure.message).toContain('服务器未返回具体原因')
    expect(api.generate).not.toHaveBeenCalled()
    expect(api.poll).not.toHaveBeenCalled()
  })

  it('reports an unavailable receipt signer without misdiagnosing Bot configuration or submitting', async () => {
    api.capabilities.mockResolvedValueOnce({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' })
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause)) as Error
    expect(failure).toBeInstanceOf(Error)
    expect(failure.message).toContain('AI_DRAFT_RECOVERY_UNAVAILABLE')
    expect(failure.message).toContain('无法签发恢复凭据')
    expect(failure.message).not.toContain('请检查服务器 effective autoHeal.botId')
    expect(api.generate).not.toHaveBeenCalled()
    expect(api.poll).not.toHaveBeenCalled()
  })

  it('does not treat capability transport failure as proof that server config is missing', async () => {
    api.capabilities.mockRejectedValueOnce(new Error('offline'))
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause as Error)) as Error
    expect(failure.message).toContain('无法确认 AI 草稿服务能力')
    expect(failure.message).toContain('不证明服务器 autoHeal.botId / BaaS 配置缺失')
    expect(failure.message).toContain('offline')
    expect(api.generate).not.toHaveBeenCalled()
    expect(api.poll).not.toHaveBeenCalled()
  })

  it.each(['input', 'output'])('enforces the 100000 UTF-8 byte limit for %s, not JS character length', async direction => {
    const huge = '汉'.repeat(34_000)
    if (direction === 'input') {
      await expect(generateWorkflowAiDraft('modify', '需求', { ...source, title: huge })).rejects.toThrow('字节')
      expect(api.capabilities).not.toHaveBeenCalled()
    } else {
      api.poll.mockResolvedValueOnce(task('completed', { result: { yaml: huge } }))
      await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow('字节')
      expect(api.validate).not.toHaveBeenCalled()
    }
  })

  it('rejects overlong prompts locally without any transport', async () => {
    await expect(generateWorkflowAiDraft('modify', 'x'.repeat(4001), source)).rejects.toThrow('4000')
    expect(api.capabilities).not.toHaveBeenCalled()
  })

  it.each([
    { workflowId: 'wrong' }, { draftId: 'wrong' }, { inputSpecDigest: 'wrong' }, { inputSpecDigest: '' },
  ])('does not accept a task with wrong binding: %j', async wrong => {
    api.poll.mockResolvedValueOnce({ ...completed(), ...wrong })
    await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow('不匹配')
    expect(api.validate).not.toHaveBeenCalled()
  })

  it('uses signed receipt task binding when SubtleCrypto is unavailable', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID() })
    await expect(generateWorkflowAiDraft('modify', '需求', source)).resolves.toHaveProperty('workflowId', source.id)
    api.poll.mockResolvedValueOnce(task('completed', { workflowId: 'other' }))
    await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow('不匹配')
  })

  it.each([new TypeError('lost response'), Object.assign(new Error('service failed'), { status: 503 })])('recovers a lost create ack using its prepared receipt only: %s', async cause => {
    const attempt = createWorkflowAiAttempt()
    api.generate.mockImplementationOnce(async (_id, input) => {
      expect(attempt).toEqual({ requestId: input.requestId, draftId: 'draft', receipt: 'signed-receipt', submitted: true })
      throw cause
    })
    const failure = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(error => error)
    expect(failure).toBeInstanceOf(WorkflowAiRecoveryError)
    expect(failure.message).toContain('BaaS 可能已接收消息')
    expect(failure.message).toContain('远端任务可能继续')
    expect(failure.message).toContain('不会重复派发')
    expect(attempt.receipt).toBe('signed-receipt')
    await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
    expect(api.prepare).toHaveBeenCalledOnce()
    expect(api.generate).toHaveBeenCalledOnce()
    expect(api.poll).toHaveBeenCalledExactlyOnceWith(source.id, 'draft', { receipt: 'signed-receipt', currentYaml: workflowAiYaml(source) }, expect.any(AbortSignal))
  })

  it.each(['transport-failed', 'available-false'] as const)('bypasses changed capabilities when recovering a known receipt: %s', async interruption => {
    const attempt = createWorkflowAiAttempt()
    api.generate.mockRejectedValueOnce(new TypeError('lost response'))
    const interrupted = await generateWorkflowAiDraft('modify', ' 原始需求\n', source, undefined, attempt).catch(cause => cause)
    expect(interrupted).toBeInstanceOf(WorkflowAiRecoveryError)
    if (interruption === 'transport-failed') api.capabilities.mockRejectedValueOnce(new Error('readiness disconnected'))
    else api.capabilities.mockResolvedValueOnce({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' })

    await expect(generateWorkflowAiDraft('modify', ' 原始需求\n', source, undefined, attempt)).resolves.toHaveProperty('summary', 'Draft')
    expect(api.prepare).toHaveBeenCalledOnce()
    expect(api.generate).toHaveBeenCalledOnce()
    expect(api.capabilities).toHaveBeenCalledOnce()
  })

  it('continues polling dispatch_uncertain at the same native message ID without another send', async () => {
    api.generate.mockResolvedValueOnce({ draftId: 'draft', status: 'dispatch_uncertain' })
    api.poll.mockResolvedValueOnce(task('dispatch_uncertain'))
    const pending = generateWorkflowAiDraft('modify', '需求', source)
    await vi.advanceTimersByTimeAsync(3000)
    await expect(pending).resolves.toHaveProperty('summary', 'Draft')
    expect(api.generate).toHaveBeenCalledOnce()
    expect(api.poll).toHaveBeenCalledTimes(2)
    expect(api.poll.mock.calls.every(([id, message, input]) => id === source.id && message === 'draft' && input.receipt === 'signed-receipt')).toBe(true)
  })

  it('recovers polling failures without re-submitting the model job', async () => {
    const attempt = createWorkflowAiAttempt()
    api.poll.mockRejectedValueOnce(new Error('temporary disconnect'))
    await expect(generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).rejects.toThrow('恢复查询同一任务')
    await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
    expect(api.generate).toHaveBeenCalledOnce()
  })

  it.each(['pending', 'running'])('polls %s to completion', async status => {
    api.poll.mockResolvedValueOnce(task(status))
    const result = generateWorkflowAiDraft('modify', '需求', source)
    await vi.advanceTimersByTimeAsync(3000)
    await expect(result).resolves.toHaveProperty('summary', 'Draft')
    expect(api.poll).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['AUTO_HEAL_BOT_UNAVAILABLE', '请检查 effective autoHeal.botId 对应的 BaaS Bot'],
    ['BAAS_AUTH_FAILED', '认证失败不能证明任务未被派发'],
    ['BAAS_REQUEST_REJECTED', 'BaaS 明确拒绝本次请求'],
    ['BAAS_GENERATION_FAILED', '检查上游任务状态或日志'],
    ['BAAS_PROVIDER_REJECTED', '检查提供方错误信息、配额、访问策略和 Bot 配置'],
    ['INVALID_GENERATION_RESULT', '无法解析或未通过工作流结构校验'],
    ['EXECUTION_TIMEOUT', '请先核对原消息状态，不要盲目重新派发'],
    ['DISPATCH_UNCERTAIN_TIMEOUT', '仍无法确认消息是否已接收'],
    ['EDIT_PERMISSION_REVOKED', '编辑权限已撤销'],
  ] as const)('maps provider code %s from failed polling to actionable guidance', async (code, guidance) => {
    api.poll.mockResolvedValueOnce(task('failed', { error: code }))
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause as Error)) as Error
    expect(failure.message).toContain(code)
    expect(failure.message).toContain(guidance)
    expect(api.validate).not.toHaveBeenCalled()
  })

  it.each(['上游返回原始诊断：请检查 Bot 输出后重试', 'constructor', 'toString', '__proto__'])('preserves unknown failed-task error %s instead of treating it as a known code', async message => {
    api.poll.mockResolvedValueOnce(task('failed', { error: message }))
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause as Error)) as Error
    expect(failure.message).toBe(message)
    expect(api.validate).not.toHaveBeenCalled()
  })

  it.each(['failed', 'cancelled', 'unknown'])('stops on %s without accepting or validating a result', async status => {
    api.poll.mockResolvedValueOnce(task(status, { error: 'failed BaaS task' }))
    await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow()
    expect(api.poll).toHaveBeenCalledOnce()
    expect(api.validate).not.toHaveBeenCalled()
  })

  it.each(['capabilities', 'prepare', 'generate', 'poll', 'validate'])('bounds non-cooperative %s at 15 minutes and best-effort cancels known jobs', async stage => {
    api[stage].mockReturnValueOnce(new Promise(() => {}))
    const promise = generateWorkflowAiDraft('modify', '需求', source)
    const rejection = expect(promise).rejects.toThrow('15 分钟')
    await vi.advanceTimersByTimeAsync(15 * 60_000)
    await rejection
    if (stage === 'generate' || stage === 'poll' || stage === 'validate') {
      expect(api.cancel).toHaveBeenCalledOnce()
      expect(api.cancel.mock.calls[0][3].aborted).toBe(false)
    } else expect(api.cancel).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels a known job on a separate signal and ignores late completion', async () => {
    const late = deferred<ReturnType<typeof completed>>()
    api.poll.mockReturnValueOnce(late.promise)
    const controller = new AbortController()
    const promise = generateWorkflowAiDraft('modify', '需求', source, controller.signal)
    const rejection = expect(promise).rejects.toThrow()
    await vi.advanceTimersByTimeAsync(0)
    const pollSignal = api.poll.mock.calls[0][3]
    controller.abort()
    await rejection
    expect(pollSignal.aborted).toBe(true)
    expect(api.cancel).toHaveBeenCalledOnce()
    expect(api.cancel.mock.calls[0][3]).not.toBe(pollSignal)
    expect(api.cancel.mock.calls[0][3].aborted).toBe(false)
    late.resolve(completed())
    await vi.advanceTimersByTimeAsync(0)
    expect(api.validate).not.toHaveBeenCalled()
  })

  it('cancels a late submission acknowledgment after the caller already cancelled', async () => {
    const late = deferred<{ draftId: string; status: string }>()
    api.generate.mockReturnValueOnce(late.promise)
    const controller = new AbortController()
    const promise = generateWorkflowAiDraft('modify', '需求', source, controller.signal)
    const rejection = expect(promise).rejects.toThrow()
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await rejection
    late.resolve({ draftId: 'draft', status: 'pending' })
    await vi.advanceTimersByTimeAsync(0)
    expect(api.cancel).toHaveBeenCalledExactlyOnceWith(source.id, 'draft', 'signed-receipt', expect.any(AbortSignal))
    expect(api.poll).not.toHaveBeenCalled()
  })

  it('bounds a failed cancellation without allowing late apply', async () => {
    api.poll.mockReturnValueOnce(new Promise(() => {}))
    api.cancel.mockReturnValueOnce(new Promise(() => {}))
    const controller = new AbortController()
    const promise = generateWorkflowAiDraft('modify', '需求', source, controller.signal)
    const rejection = expect(promise).rejects.toThrow()
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await rejection
    await vi.advanceTimersByTimeAsync(10_000)
    expect(api.cancel.mock.calls[0][3].aborted).toBe(true)
    expect(api.validate).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops local waiting even when best-effort server cancellation fails', async () => {
    api.poll.mockReturnValueOnce(new Promise(() => {}))
    api.cancel.mockRejectedValueOnce(new Error('server cancellation failed'))
    const controller = new AbortController()
    const promise = generateWorkflowAiDraft('modify', '需求', source, controller.signal)
    const rejection = expect(promise).rejects.toThrow()
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await rejection
    await Promise.resolve()
    expect(api.cancel).toHaveBeenCalledOnce()
    expect(api.cancel.mock.calls[0][3].aborted).toBe(false)
    expect(api.generate).toHaveBeenCalledOnce()
    expect(api.poll).toHaveBeenCalledOnce()
    expect(api.validate).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('describes local cancellation without promising that BaaS stopped', async () => {
    api.poll.mockResolvedValueOnce(task('cancelled'))
    const failure = (await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause as Error)) as Error
    expect(failure.message).toContain('取消不会持久记录')
    expect(failure.message).not.toContain('服务器记录为取消')
    expect(failure.message).toContain('BaaS 无支持的停止接口')
    expect(failure.message).toContain('远端任务可能继续')
    expect(api.validate).not.toHaveBeenCalled()
  })
})

it('keeps a signed receipt for a submission whose response times out', async () => {
  const attempt = createWorkflowAiAttempt()
  api.generate.mockReturnValueOnce(new Promise(() => {}))
  const pending = generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  const failure = expect(pending).rejects.toBeInstanceOf(WorkflowAiRecoveryError)
  await vi.advanceTimersByTimeAsync(15 * 60_000)
  await failure
  await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  expect(api.prepare).toHaveBeenCalledOnce()
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.poll.mock.calls[0][1]).toBe('draft')
})

it('accepts 4000 Unicode code points without trimming the raw requirement', async () => {
  const prompt = '🙂'.repeat(4000)
  await generateWorkflowAiDraft('modify', prompt, source)
  expect(api.generate.mock.calls[0][1].prompt).toBe(prompt)
})

it('preserves version value and type through local and normalized validation', async () => {
  api.poll.mockResolvedValueOnce(task('completed', { result: { yaml: stringify({ ...source, version: 1 }) } }))
  await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow('版本')
  expect(api.validate).not.toHaveBeenCalled()
})

it('computes the SHA-256 digest over the exact UTF-8 YAML bytes with native SubtleCrypto', async () => {
  const { workflowAiInputDigest } = await import('./workflow-ai-lifecycle')
  vi.stubGlobal('crypto', webcrypto)
  const yaml = 'id: 汉字\n# do not normalize\r\n'
  expect(await workflowAiInputDigest(yaml)).toBe(digest(yaml))
})

it.each([
  new TypeError('validation disconnected'),
  Object.assign(new Error('validation service unavailable'), { status: 503 }),
  Object.assign(new Error('validation authentication required'), { status: 401 }),
  Object.assign(new Error('validation forbidden'), { status: 403 }),
  Object.assign(new Error('validation rate limited'), { status: 429 }),
])('recovers a completed draft after validation operational HTTP/transport failure: %s', async failure => {
  const attempt = createWorkflowAiAttempt()
  api.validate.mockRejectedValueOnce(failure)
  const error = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(cause => cause)
  expect(error).toBeInstanceOf(WorkflowAiRecoveryError)
  expect(error.attempt).toBe(attempt)
  expect(attempt.draftId).toBe('draft')
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.validate).toHaveBeenCalledOnce()

  const recovered = await generateWorkflowAiDraft('modify', '需求', source, undefined, error.attempt)
  expect(recovered.spec).toEqual(source)
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.capabilities).toHaveBeenCalledOnce()
  expect(api.poll.mock.calls.map(([workflowId, draftId]) => [workflowId, draftId])).toEqual([
    [source.id, 'draft'], [source.id, 'draft'],
  ])
  expect(api.validate).toHaveBeenCalledTimes(2)
  expect(api.cancel).not.toHaveBeenCalled()
})

it.each(['invalid-yaml', 'validation-rejection', 'invalid-normalized-spec'])('keeps deterministic %s candidate errors non-recoverable', async failure => {
  if (failure === 'invalid-yaml') api.poll.mockResolvedValueOnce(task('completed', { result: { yaml: 'null' } }))
  if (failure === 'validation-rejection') api.validate.mockResolvedValueOnce({ valid: false, normalizedSpec: null, issues: [{ path: 'nodes', message: 'Invalid configuration', severity: 'error' }] })
  if (failure === 'invalid-normalized-spec') api.validate.mockResolvedValueOnce({ valid: true, normalizedSpec: { ...source, id: 'other' }, issues: [] })
  const error = await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause)
  expect(error).toBeInstanceOf(Error)
  expect(error).not.toBeInstanceOf(WorkflowAiRecoveryError)
  expect(api.generate).toHaveBeenCalledOnce()
})

it('does not infer an invalid candidate from a bare validation HTTP 422 transport failure', async () => {
  const attempt = createWorkflowAiAttempt()
  api.validate.mockRejectedValueOnce(Object.assign(new Error('Invalid candidate'), { status: 422 }))
  const failure = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(cause => cause)
  expect(failure).toBeInstanceOf(WorkflowAiRecoveryError)
  expect(failure.attempt).toBe(attempt)
  expect(attempt.draftId).toBe('draft')

  await generateWorkflowAiDraft('modify', '需求', source, undefined, failure.attempt)
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.validate).toHaveBeenCalledTimes(2)
})


it.each([
  { draftId: '' }, { draftId: ' ' }, { draftId: null }, { receipt: '' }, { receipt: ' ' }, { receipt: null },
  { inputSpecDigest: '' }, { inputSpecDigest: 'wrong' }, { inputSpecDigest: 'b'.repeat(64) },
])('fails closed on malformed or wrongly bound prepare responses: %j', async wrong => {
  api.prepare.mockResolvedValueOnce({ draftId: 'draft', receipt: 'signed-receipt', inputSpecDigest: digest(workflowAiYaml(source)), ...wrong })
  await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toBeInstanceOf(WorkflowAiCandidateError)
  expect(api.generate).not.toHaveBeenCalled()
  expect(api.poll).not.toHaveBeenCalled()
})

it.each(['', 'invalid', null])('still requires a nonempty 64-hex prepare digest without SubtleCrypto: %s', async inputSpecDigest => {
  vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID() })
  api.prepare.mockResolvedValueOnce({ draftId: 'draft', receipt: 'signed-receipt', inputSpecDigest })
  await expect(generateWorkflowAiDraft('modify', '需求', source)).rejects.toThrow('摘要不匹配')
  expect(api.generate).not.toHaveBeenCalled()
})

it('retries prepare explicitly after a failure that cannot have dispatched BaaS', async () => {
  const attempt = createWorkflowAiAttempt()
  api.prepare.mockRejectedValueOnce(new Error('signer offline'))
  const error = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(cause => cause)
  expect(error).not.toBeInstanceOf(WorkflowAiRecoveryError)
  expect(error.message).toContain('尚未派发生成请求')
  expect(attempt).toEqual({ requestId: attempt.requestId })
  expect(api.generate).not.toHaveBeenCalled()
  await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  expect(api.prepare).toHaveBeenCalledTimes(2)
  expect(api.prepare.mock.calls[0][1]).toEqual(api.prepare.mock.calls[1][1])
  expect(api.generate).toHaveBeenCalledOnce()
})

it.each([{ submitted: true }, { draftId: 'legacy-id' }, { submitted: true, draftId: 'legacy-id' }, { receipt: 'receipt-only' }])('cannot recover a legacy attempt or prepare a replacement without full context: %j', async old => {
  const attempt = { requestId: 'legacy', ...old }
  for (let count = 0; count < 2; count++) {
    await expect(generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).rejects.toThrow('无法恢复')
  }
  expect(api.capabilities).not.toHaveBeenCalled()
  expect(api.prepare).not.toHaveBeenCalled()
  expect(api.generate).not.toHaveBeenCalled()
  expect(api.poll).not.toHaveBeenCalled()
})

it('never replaces the prepared ID with a different create acknowledgment', async () => {
  const attempt = createWorkflowAiAttempt()
  api.generate.mockResolvedValueOnce({ draftId: 'unexpected-id', status: 'pending' })
  await expect(generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).rejects.toThrow('任务 ID 不匹配')
  expect(attempt.draftId).toBe('draft')
  expect(attempt.receipt).toBe('signed-receipt')
  expect(api.poll).not.toHaveBeenCalled()
  await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.poll.mock.calls[0][1]).toBe('draft')
})

it('treats create failed+error as an explicit terminal rejection without polling', async () => {
  api.generate.mockResolvedValueOnce({ draftId: 'draft', status: 'failed', error: 'BAAS_REQUEST_REJECTED' })
  const error = await generateWorkflowAiDraft('modify', '需求', source).catch(cause => cause)
  expect(error).toBeInstanceOf(WorkflowAiCandidateError)
  expect(error.message).toContain('BAAS_REQUEST_REJECTED')
  expect(error.message).toContain('明确拒绝')
  expect(api.poll).not.toHaveBeenCalled()
  expect(api.validate).not.toHaveBeenCalled()
})

it.each(['cancel', 'timeout'])('never dispatches a late prepare acknowledgment after %s', async action => {
  const late = deferred<{ draftId: string; receipt: string; inputSpecDigest: string }>()
  api.prepare.mockReturnValueOnce(late.promise)
  const controller = new AbortController()
  const attempt = createWorkflowAiAttempt()
  const pending = generateWorkflowAiDraft('modify', '需求', source, controller.signal, attempt)
  const rejected = expect(pending).rejects.toThrow()
  await vi.advanceTimersByTimeAsync(0)
  if (action === 'cancel') controller.abort()
  else await vi.advanceTimersByTimeAsync(15 * 60_000)
  await rejected
  late.resolve({ draftId: 'draft', receipt: 'signed-receipt', inputSpecDigest: digest(workflowAiYaml(source)) })
  await vi.advanceTimersByTimeAsync(0)
  expect(attempt.receipt).toBeUndefined()
  expect(attempt.submitted).not.toBe(true)
  expect(api.generate).not.toHaveBeenCalled()
  expect(api.poll).not.toHaveBeenCalled()
  expect(api.validate).not.toHaveBeenCalled()
  expect(api.cancel).toHaveBeenCalledExactlyOnceWith(source.id, 'draft', 'signed-receipt', expect.any(AbortSignal))
  // Only an explicit user retry can prepare and dispatch a new request.
  await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  expect(api.prepare).toHaveBeenCalledTimes(2)
  expect(api.generate).toHaveBeenCalledOnce()
})

it('recovers from a new service instance using only the existing session receipt', async () => {
  const attempt = createWorkflowAiAttempt()
  api.generate.mockRejectedValueOnce(new Error('lost create ack'))
  await expect(generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).rejects.toThrow('请求未确认')
  vi.resetModules()
  const fresh = await import('./workflow-ai-draft')
  await expect(fresh.generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).resolves.toHaveProperty('summary', 'Draft')
  expect(api.prepare).toHaveBeenCalledOnce()
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.capabilities).toHaveBeenCalledOnce()
})


it('does not let a timed-out prepare overwrite a newer explicit retry of the same attempt', async () => {
  const oldPrepare = deferred<{ draftId: string; receipt: string; inputSpecDigest: string }>()
  api.prepare.mockReturnValueOnce(oldPrepare.promise)
  const attempt = createWorkflowAiAttempt()
  const first = generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  const rejected = expect(first).rejects.toThrow('尚未派发生成请求')
  await vi.advanceTimersByTimeAsync(15 * 60_000)
  await rejected
  const pendingPoll = deferred<ReturnType<typeof completed>>()
  api.poll.mockReturnValueOnce(pendingPoll.promise)
  const retry = generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)
  await vi.advanceTimersByTimeAsync(0)
  oldPrepare.resolve({ draftId: 'old-unsent-id', receipt: 'old-receipt', inputSpecDigest: digest(workflowAiYaml(source)) })
  await vi.advanceTimersByTimeAsync(0)
  expect(attempt).toEqual({ requestId: attempt.requestId, draftId: 'draft', receipt: 'signed-receipt', submitted: true })
  pendingPoll.resolve(completed())
  await expect(retry).resolves.toHaveProperty('summary', 'Draft')
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.cancel).toHaveBeenCalledWith(source.id, 'old-unsent-id', 'old-receipt', expect.any(AbortSignal))
})


it('dispatches once even if two callers share the same in-memory attempt', async () => {
  const attempt = createWorkflowAiAttempt()
  api.prepare.mockResolvedValueOnce({ draftId: 'draft', receipt: 'signed-receipt', inputSpecDigest: digest(workflowAiYaml(source)) })
    .mockResolvedValueOnce({ draftId: 'unused-id', receipt: 'unused-receipt', inputSpecDigest: digest(workflowAiYaml(source)) })
  await Promise.all([
    generateWorkflowAiDraft('modify', '需求', source, undefined, attempt),
    generateWorkflowAiDraft('modify', '需求', source, undefined, attempt),
  ])
  expect(api.generate).toHaveBeenCalledOnce()
  expect(attempt).toEqual({ requestId: attempt.requestId, draftId: 'draft', receipt: 'signed-receipt', submitted: true })
  expect(api.poll.mock.calls.every(([, id, input]) => id === 'draft' && input.receipt === 'signed-receipt')).toBe(true)
})

it.each(['HTTP 400: binding rejected', '60001: binding rejected after acceptance'])('polls the original receipt after uncertain rejection and accepts only its real terminal failure: %s', async error => {
  const attempt = createWorkflowAiAttempt()
  api.generate.mockResolvedValueOnce({ draftId: 'draft', status: 'dispatch_uncertain' })
  api.poll.mockResolvedValueOnce(task('failed', { error }))
  const failure = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(cause => cause)
  expect(failure).toBeInstanceOf(WorkflowAiCandidateError)
  expect(failure.message).toBe(error)
  expect(api.prepare).toHaveBeenCalledOnce()
  expect(api.generate).toHaveBeenCalledOnce()
  expect(api.poll).toHaveBeenCalledExactlyOnceWith(source.id, 'draft', { receipt: 'signed-receipt', currentYaml: workflowAiYaml(source) }, expect.any(AbortSignal))
  expect(api.validate).not.toHaveBeenCalled()
  expect(attempt.draftId).toBe('draft')
  expect(attempt.receipt).toBe('signed-receipt')
})

it.each(['BAAS_AUTH_FAILED', 'BAAS_POLL_UNAVAILABLE'])('recovers an operational poll error %s without dropping the signed receipt or sending again', async code => {
  const attempt = createWorkflowAiAttempt();
  api.poll.mockResolvedValueOnce(task('running', { error: code }));
  const failure = await generateWorkflowAiDraft('modify', '需求', source, undefined, attempt).catch(cause => cause);
  expect(failure).toBeInstanceOf(WorkflowAiRecoveryError);
  expect(failure.attempt).toBe(attempt);
  expect(failure.message).toContain(code);
  expect(attempt).toMatchObject({ draftId: 'draft', receipt: 'signed-receipt', submitted: true });
  expect(api.prepare).toHaveBeenCalledOnce(); expect(api.generate).toHaveBeenCalledOnce();
  await expect(generateWorkflowAiDraft('modify', '需求', source, undefined, attempt)).resolves.toHaveProperty('workflowId', source.id);
  expect(api.prepare).toHaveBeenCalledOnce(); expect(api.generate).toHaveBeenCalledOnce();
  expect(api.poll).toHaveBeenCalledTimes(2);
  expect(api.poll.mock.calls[1].slice(0, 3)).toEqual(api.poll.mock.calls[0].slice(0, 3));
});
