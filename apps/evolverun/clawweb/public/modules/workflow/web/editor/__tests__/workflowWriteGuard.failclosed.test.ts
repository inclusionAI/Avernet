import { afterEach, expect, it, vi } from 'vitest'
import { abandonEditorWorkflowWrite, assertEditorWorkflowReadable, beginEditorWorkflowWrite, editorWorkflowWriteState, finishEditorWorkflowWrite } from '../workflowWriteGuard'

let number = 0
const id = () => `guard-test-${++number}-${crypto.randomUUID()}`
const marker = (workflowId: string) => `avernet.editor.pending-workflow-write.v1:${encodeURIComponent(workflowId)}`
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })

it('persists before dispatch, retains unknown, and cannot clear using another owner', () => {
  const workflowId = id(), owner = beginEditorWorkflowWrite(workflowId)
  expect(JSON.parse(sessionStorage.getItem(marker(workflowId))!)).toEqual({ version: 1, owner })
  expect(() => finishEditorWorkflowWrite(workflowId, 'not-owner')).toThrow(/标记/)
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(() => finishEditorWorkflowWrite(workflowId, owner)).toThrow()
  expect(sessionStorage.getItem(marker(workflowId))).not.toBeNull()
  expect(() => assertEditorWorkflowReadable(workflowId)).toThrow(/人工核对/)
})

it('interprets a persisted in-flight marker in a fresh module state as unknown, not a successful read', () => {
  const workflowId = id()
  sessionStorage.setItem(marker(workflowId), JSON.stringify({ version: 1, owner: 'previous-page' }))
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(() => assertEditorWorkflowReadable(workflowId)).toThrow(/人工核对/)
})

it('rejects dispatch if durable marker storage fails', () => {
  const workflowId = id()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  expect(() => beginEditorWorkflowWrite(workflowId)).toThrow(/未发送保存/)
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it('fails closed on storage read errors and invalid or unexpected markers', () => {
  const blocked = id(), corrupted = id()
  sessionStorage.setItem(marker(corrupted), '{broken')
  expect(editorWorkflowWriteState(corrupted).phase).toBe('unknown')
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
  expect(editorWorkflowWriteState(blocked).phase).toBe('unknown')
  expect(() => beginEditorWorkflowWrite(blocked)).toThrow(/人工核对/)
})

it('keeps the marker for an abandoned operation and clears a matching terminal owner', () => {
  const unknown = id(), terminal = id()
  const abandoned = beginEditorWorkflowWrite(unknown)
  abandonEditorWorkflowWrite(unknown, abandoned)
  expect(sessionStorage.getItem(marker(unknown))).not.toBeNull()
  expect(editorWorkflowWriteState(unknown).phase).toBe('unknown')
  const owner = beginEditorWorkflowWrite(terminal)
  finishEditorWorkflowWrite(terminal, owner)
  expect(sessionStorage.getItem(marker(terminal))).toBeNull()
  expect(editorWorkflowWriteState(terminal).phase).toBe('clear')
})

it('recreates unknown from an actual dispatched marker after a same-tab module reload', async () => {
  const workflowId = id()
  beginEditorWorkflowWrite(workflowId)
  const symbol = Symbol.for('avernet.editor.workflow-write-guard.v1')
  const root = globalThis as typeof globalThis & { [key: symbol]: unknown }
  delete root[symbol]
  vi.resetModules()
  const reloaded = await import('../workflowWriteGuard')
  expect(reloaded.editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(() => reloaded.assertEditorWorkflowReadable(workflowId)).toThrow(/人工核对/)
})


it('creates a secure write owner without randomUUID when getRandomValues is available', () => {
  const workflowId = id()
  const getRandomValues = crypto.getRandomValues.bind(crypto)
  vi.stubGlobal('crypto', { getRandomValues })
  const owner = beginEditorWorkflowWrite(workflowId)
  expect(owner).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
  expect(JSON.parse(sessionStorage.getItem(marker(workflowId))!)).toEqual({ version: 1, owner })
  finishEditorWorkflowWrite(workflowId, owner)
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
})

it('fails closed when secure randomness is unavailable', () => {
  const workflowId = id()
  vi.stubGlobal('crypto', {})
  expect(() => beginEditorWorkflowWrite(workflowId)).toThrow(/未发送保存/)
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(sessionStorage.getItem(marker(workflowId))).toBeNull()
})
