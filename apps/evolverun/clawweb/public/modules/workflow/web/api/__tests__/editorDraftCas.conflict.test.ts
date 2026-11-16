import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { editorDraftToken } from '../../../server/services/editor-draft-token'
import { readDatabaseWorkflowDraft, readEditorWorkflowSource } from '../editorWorkflowSource'
import { editorWorkflowWriteState } from '../../editor/workflowWriteGuard'
import { saveVerifiedWorkflowDocument, WorkflowDocumentAcknowledgedError } from '../../editor/workflowDocumentSave'

let sequence = 0
let spec: { id: string; title: string; version: string; nodes: never[] }
let row: { workflowId: string; packId: null; specJson: string; draftToken: string }
const marker = () => `avernet.editor.pending-workflow-write.v1:${encodeURIComponent(spec.id)}`
beforeEach(() => {
  // Unknown outcomes deliberately persist within a workflow; each case models
  // a different workflow rather than resetting the production in-memory guard.
  const id = `draft-conflict-fixture-${++sequence}`
  spec = { id, title: 'Original', version: '1', nodes: [] }
  const databaseRow = { id: 1, workflow_id: id, pack_id: null, spec_json: JSON.stringify(spec), version: 1, title: spec.title, owner_id: null, gmt_create: 1, gmt_modified: 1 }
  row = { workflowId: id, packId: null, specJson: databaseRow.spec_json, draftToken: editorDraftToken(databaseRow) }
})
afterEach(() => { sessionStorage.removeItem(marker()); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function attempt(put: () => unknown) {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(spec))).mockResolvedValueOnce(new Response(JSON.stringify(row)))
  vi.stubGlobal('fetch', fetchMock)
  const baseline = await readDatabaseWorkflowDraft(spec.id)
  expect(baseline).toEqual(spec)
  fetchMock.mockClear().mockResolvedValueOnce(new Response(JSON.stringify(row))).mockImplementationOnce(put)
  const saving = saveVerifiedWorkflowDocument(spec.id, baseline, { ...spec, title: 'Local' }, () => true)
  return { saving, fetchMock }
}
it('recognizes only a readable real 409 DRAFT_CONFLICT response as conflict', async () => {
  const { saving, fetchMock } = await attempt(() => Promise.resolve(new Response('{"code":"DRAFT_CONFLICT"}', { status: 409 })))
  await expect(saving).rejects.toThrow('保存冲突')
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(editorWorkflowWriteState(spec.id).phase).toBe('clear')
  expect(sessionStorage.getItem(marker())).toBeNull()
})
it.each(['thrown', 'other-code', 'unreadable', 'fake-response'] as const)('treats %s 409 as unknown, with no retry', async kind => {
  const response = new Response('{"code":"DRAFT_CONFLICT"}', { status: 409 })
  if (kind === 'unreadable') vi.spyOn(response, 'text').mockRejectedValue(new Error('lost'))
  const { saving, fetchMock } = await attempt(() => kind === 'thrown' ? Promise.reject({ status: 409, body: '{"code":"DRAFT_CONFLICT"}' })
    : kind === 'other-code' ? Promise.resolve(new Response('{"code":"OTHER"}', { status: 409 }))
      : kind === 'fake-response' ? Promise.resolve({ ok: false, status: 409, text: async () => '{"code":"DRAFT_CONFLICT"}' }) : Promise.resolve(response))
  await expect(saving).rejects.toThrow('结果未知')
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(editorWorkflowWriteState(spec.id).phase).toBe('unknown')
  expect(sessionStorage.getItem(marker())).not.toBeNull()
  await expect(readEditorWorkflowSource(spec.id)).rejects.toThrow('人工核对')
})
it.each(['wrong-spec', 'malformed-json'] as const)('reports acknowledged but unverifiable %s without retry', async kind => {
  const { saving, fetchMock } = await attempt(() => Promise.resolve(new Response(kind === 'wrong-spec'
    ? JSON.stringify({ ...row, draftToken: 'b'.repeat(64) }) : '{', { status: 200 })))
  const error = await saving.catch(value => value)
  expect(error).toBeInstanceOf(WorkflowDocumentAcknowledgedError)
  expect(editorWorkflowWriteState(spec.id).phase).toBe('unknown')
  await expect(readEditorWorkflowSource(spec.id)).rejects.toThrow('人工核对')
  expect(error.message).toMatch(/可能.*写入/); expect(error.message).toContain('先核对'); expect(error.message).toContain('勿盲目重试')
  if (kind === 'wrong-spec') expect(error.message).toContain('保存内容与请求不同')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
