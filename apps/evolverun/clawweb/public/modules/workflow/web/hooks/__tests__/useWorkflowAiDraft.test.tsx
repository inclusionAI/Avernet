import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { generateWorkflowAiDraft, workflowSpecSignature, type WorkflowAiDraft, type WorkflowAiMode } from '../../services/workflow-ai-draft'
import { useWorkflowAiDraft } from '../useWorkflowAiDraft'

vi.mock('../../services/workflow-ai-draft', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/workflow-ai-draft')>()
  return { ...actual, generateWorkflowAiDraft: vi.fn() }
})
const generate = vi.mocked(generateWorkflowAiDraft)
const spec: WorkflowSpec = { id: 'flow-a', title: 'Flow A', version: '1', nodes: [] }
const draft: WorkflowAiDraft = {
  workflowId: spec.id, baseSignature: workflowSpecSignature(spec), summary: 'A draft', spec, yaml: 'nodes: []',
  changes: [], warnings: [], metadataChanges: [], removedNodeIds: [],
}
function deferred() {
  let resolve!: (value: WorkflowAiDraft) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<WorkflowAiDraft>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function setup() {
  return renderHook(({ open, mode, source }: { open: boolean; mode: WorkflowAiMode; source: WorkflowSpec | null }) =>
    useWorkflowAiDraft(open, mode, source), { initialProps: { open: true, mode: 'modify' as WorkflowAiMode, source: spec as WorkflowSpec | null } })
}
function start(result: ReturnType<typeof setup>['result']) {
  act(() => { result.current.changePrompt('调整当前流程') })
  act(() => { void result.current.generate() })
}
beforeEach(() => { generate.mockReset(); generate.mockResolvedValue(draft) })
afterEach(cleanup)

describe('useWorkflowAiDraft request lifetime', () => {
  it.each(['close', 'workflow', 'spec', 'mode', 'prompt', 'cancel'] as const)('aborts and ignores results after %s changes', async (change) => {
    const pending = deferred()
    generate.mockReturnValueOnce(pending.promise)
    const { result, rerender } = setup()
    start(result)
    expect(result.current.busy).toBe(true)
    const signal = generate.mock.calls[0][3]
    if (change === 'close') rerender({ open: false, mode: 'modify', source: spec })
    if (change === 'workflow') rerender({ open: true, mode: 'modify', source: { ...spec, id: 'flow-b' } })
    if (change === 'spec') rerender({ open: true, mode: 'modify', source: { ...spec, title: 'Edited' } })
    if (change === 'mode') act(() => { result.current.changeMode('create') })
    if (change === 'prompt') act(() => { result.current.changePrompt('另一个请求') })
    if (change === 'cancel') act(() => { result.current.cancel() })
    expect(signal?.aborted).toBe(true)
    expect(result.current.busy).toBe(false)
    await act(async () => { pending.resolve(draft) })
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('does not let an older response or finally block replace a newer in-flight request', async () => {
    const first = deferred()
    const second = deferred()
    generate.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result } = setup()
    start(result)
    act(() => { result.current.changePrompt('第二个请求') })
    act(() => { void result.current.generate() })
    await act(async () => { first.resolve(draft) })
    expect(result.current.result).toBeNull()
    expect(result.current.busy).toBe(true)
    const newer = { ...draft, summary: 'Newer result' }
    await act(async () => { second.resolve(newer) })
    expect(result.current.result?.draft).toEqual(newer)
    expect(result.current.busy).toBe(false)
  })

  it('ignores late rejection after close and reopen', async () => {
    const pending = deferred()
    generate.mockReturnValueOnce(pending.promise)
    const { result, rerender } = setup()
    start(result)
    rerender({ open: false, mode: 'modify', source: spec })
    rerender({ open: true, mode: 'modify', source: spec })
    await act(async () => { pending.reject(new Error('Old error')) })
    expect(result.current.error).toBeNull()
    expect(result.current.prompt).toBe('')
    expect(result.current.result).toBeNull()
  })

  it('aborts on unmount even when the transport ignores the signal', async () => {
    const pending = deferred()
    generate.mockReturnValueOnce(pending.promise)
    const { result, unmount } = setup()
    start(result)
    const signal = generate.mock.calls[0][3]
    unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => { pending.resolve(draft) })
  })

  it('guards repeated synchronous submissions and clears completed drafts before every new request', async () => {
    const { result } = setup()
    act(() => { result.current.changePrompt('first') })
    await act(async () => {
      void result.current.generate()
      void result.current.generate()
    })
    expect(generate).toHaveBeenCalledTimes(1)
    expect(result.current.result?.draft).toEqual(draft)
    const next = deferred()
    generate.mockReturnValueOnce(next.promise)
    act(() => { void result.current.generate() })
    expect(result.current.result).toBeNull()
    expect(result.current.busy).toBe(true)
    await act(async () => { next.reject(new Error('Failed retry')) })
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBe('Failed retry')
  })

  it('rejects drafts for a different workflow without making them applicable', async () => {
    generate.mockResolvedValueOnce({ ...draft, workflowId: 'flow-b' })
    const { result } = setup()
    act(() => { result.current.changePrompt('修改') })
    await act(async () => { await result.current.generate() })
    expect(result.current.error).toBe('生成结果不属于当前工作流，请重试')
    expect(result.current.result).toBeNull()
  })

  it('keeps a completed preview stale on content edits but accepts equivalent spec objects', async () => {
    const { result, rerender } = setup()
    act(() => { result.current.changePrompt('修改') })
    await act(async () => { await result.current.generate() })
    rerender({ open: true, mode: 'modify', source: structuredClone(spec) })
    expect(result.current.stale).toBe(false)
    rerender({ open: true, mode: 'modify', source: { ...spec, title: 'Locally edited' } })
    expect(result.current.result?.draft).toEqual(draft)
    expect(result.current.stale).toBe(true)
  })

  it('validates blank prompts without contacting the generator', async () => {
    const { result } = setup()
    act(() => { result.current.changePrompt('   ') })
    await act(async () => { await result.current.generate() })
    expect(result.current.error).toBe('请输入想增加或修改的工作流能力')
    expect(generate).not.toHaveBeenCalled()
  })
})

it('passes raw prompts and preserves an unknown/uncertain attempt identity when explicitly recovering', async () => {
  const { WorkflowAiRecoveryError } = await import('../../services/workflow-ai-lifecycle')
  const attempt = { requestId: '0b6da915-b372-44cb-90fc-3fe721098fe6', draftId: 'persisted-draft' }
  generate.mockRejectedValueOnce(new WorkflowAiRecoveryError('派发结果不确定', attempt))
  const { result } = setup()
  act(() => result.current.changePrompt('  原始需求\n'))
  await act(async () => { await result.current.generate() })
  expect(result.current.recoverable).toBe(true)
  expect(result.current.result).toBeNull()
  await act(async () => { await result.current.generate() })
  expect(generate.mock.calls[0][1]).toBe('  原始需求\n')
  expect(generate.mock.calls[1][4]).toBe(attempt)
  expect(result.current.result?.draft).toEqual(draft)
})

it('keeps the same attempt through a generic operational readiness failure and completes with the same requestId', async () => {
  const { WorkflowAiRecoveryError } = await import('../../services/workflow-ai-lifecycle')
  const attempt = { requestId: '0b6da915-b372-44cb-90fc-3fe721098fe6' }
  generate.mockRejectedValueOnce(new WorkflowAiRecoveryError('BaaS 提交结果不确定', attempt))
    .mockRejectedValueOnce(new Error('无法确认 AI 草稿服务能力'))
  const { result } = setup()
  act(() => result.current.changePrompt(' 原始需求\n'))
  await act(async () => { await result.current.generate() })
  expect(result.current.recoverable).toBe(true)
  await act(async () => { await result.current.generate() })
  expect(result.current.recoverable).toBe(true)
  expect(result.current.result).toBeNull()
  await act(async () => { await result.current.generate() })
  expect(generate).toHaveBeenCalledTimes(3)
  expect(generate.mock.calls[0][1]).toBe(' 原始需求\n')
  expect(generate.mock.calls[1][4]).toBe(attempt)
  expect(generate.mock.calls[2][4]).toBe(attempt)
  expect(result.current.result?.draft).toEqual(draft)
})

it('clears only explicitly deterministic candidate failures while preserving operational errors', async () => {
  const { WorkflowAiCandidateError } = await import('../../services/workflow-ai-draft')
  generate.mockRejectedValueOnce(new Error('operational failure after submission'))
    .mockRejectedValueOnce(new WorkflowAiCandidateError('服务端返回 INVALID_GENERATION_RESULT'))
  const { result } = setup()
  act(() => result.current.changePrompt('需求'))
  await act(async () => { await result.current.generate() })
  expect(result.current.recoverable).toBe(true)
  await act(async () => { await result.current.generate() })
  expect(result.current.recoverable).toBe(false)
  await act(async () => { await result.current.generate() })
  expect(generate.mock.calls[0][4]).not.toBe(generate.mock.calls[2][4])
  expect(result.current.result?.draft).toEqual(draft)
})

it('invalidates in-flight requests and completed previews on layout/source binding changes', async () => {
  const pending = deferred()
  generate.mockReturnValueOnce(pending.promise)
  const view = renderHook(({ binding }) => useWorkflowAiDraft(true, 'modify', spec, binding), { initialProps: { binding: 'session-1:layout-1' } })
  start(view.result)
  const signal = generate.mock.calls[0][3]!
  view.rerender({ binding: 'session-1:layout-2' })
  expect(signal.aborted).toBe(true)
  await act(async () => { pending.resolve(draft) })
  expect(view.result.current.result).toBeNull()
  await act(async () => { await view.result.current.generate() })
  expect(view.result.current.stale).toBe(false)
  view.rerender({ binding: 'session-2:layout-2' })
  expect(view.result.current.stale).toBe(true)
})

it('assigns a fresh preview identity to every completed generation', async () => {
  const { result } = setup()
  act(() => result.current.changePrompt('需求'))
  await act(async () => { await result.current.generate() })
  const firstId = result.current.result!.requestId
  await act(async () => { await result.current.generate() })
  expect(result.current.result!.requestId).not.toBe(firstId)
})
