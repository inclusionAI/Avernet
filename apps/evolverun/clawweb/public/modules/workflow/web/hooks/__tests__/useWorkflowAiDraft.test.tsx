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
