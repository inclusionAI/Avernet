import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { candidateProjection } from '../../../server/services/pack-operation-service'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../store'
import { saveWorkflowCanvasDocument } from '../workflowDocumentSave'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => null }))
const id = 'trusted-recovery-first-draft', packId = 'pack'
const authored = { id, title: 'Original', nodes: [
  { id: 'start', title: 'Start', phase: 'execute', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', phase: 'execute', executor: { type: 'end' }, dependsOn: ['start'] },
], content: 'id: nested\nnodes: []\n', custom: { keep: [false, null, 0] } }
const yaml = stringify(authored)
const archive = candidateProjection(id, yaml, '{}').specJson
const token = createHash('sha256').update(JSON.stringify(['active-workflow-baseline-v1', id, packId, '3', '7', 'v7', 'deploy', archive])).digest('hex')
const active = { deployNumber: 3, version: 7, isActive: true, packId, tagName: 'v7', action: 'deploy', activeToken: token }
const snapshot = { workflowId: id, deployNumber: 3, version: 7, tagName: 'v7', action: 'deploy', specJson: archive }
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const blobText = (blob: Blob) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(blob)
})
let draft: typeof authored | null, currentActive: Record<string, unknown>, currentSnapshot: Record<string, unknown>
let onHistory: (() => void) | undefined, onSnapshot: (() => void) | undefined, beforePost: (() => void) | undefined
let sent: Record<string, unknown>[], gitToken: string, gitYaml: string
const state = () => useEditorStore.getState()
beforeEach(() => {
  const guard = (globalThis as typeof globalThis & { [key: symbol]: { entries: Map<string, unknown> } | undefined })[Symbol.for('avernet.editor.workflow-write-guard.v1')]
  guard?.entries.delete(id); sessionStorage.clear(); state().reset({ force: true })
  draft = null; currentActive = { ...active }; currentSnapshot = { ...snapshot }; sent = []; gitToken = token; gitYaml = yaml
  onHistory = undefined; onSnapshot = undefined; beforePost = undefined
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === `/api/workflows/${id}`) return draft ? json(draft) : json({ code: 'WORKFLOW_NOT_FOUND', workflowId: id }, 404)
    if (url === `/api/workflows/${id}/editor-draft`) return json({ workflowId: id, packId, specJson: JSON.stringify(draft), draftToken: 'a'.repeat(64) })
    if (url.includes('/history?')) { onHistory?.(); return json({ workflowId: id, history: [], active: currentActive }) }
    if (url.endsWith('/by-deploy/3')) { onSnapshot?.(); return json(currentSnapshot) }
    if (url === `/api/workflows/${id}/pack-version?version=current`) return json({
      workflowId: id, packId, baseRef: `refs/tags/deploy/${id}/#1`, baseCommit: 'b'.repeat(40), draftHead: null,
      activeHistory: { protocol: 'active-history-v1', activeToken: gitToken },
      versionFiles: [
        { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: packId, workflows: [id] })), mode: '100644' },
        { path: `workflows/${id}.yaml`, contentBase64: btoa(gitYaml), mode: '100644' },
      ],
    })
    if (url === '/api/auto-heal/editor-save-first-draft') {
      const request = JSON.parse(await blobText(init!.body as Blob)) as Record<string, unknown>
      sent.push(request); beforePost?.()
      // Model the endpoint's atomic absent-draft + active-token check.
      if (draft) return json({ code: 'DRAFT_ALREADY_EXISTS' }, 409)
      if (currentActive.activeToken !== request.expectedActiveToken) return json({ code: 'BASELINE_STALE' }, 409)
      draft = request.spec as typeof authored
      return json({ ok: true, saved: true, workflowId: id, packId })
    }
    throw new Error(`Unexpected request ${url}`)
  }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function open() {
  const reader = vi.fn(async (identity: Parameters<NonNullable<Parameters<typeof readEditorWorkflowSource>[1]>['readTrustedActiveHistory']>[0]) => ({ ...identity, workflowText: yaml }))
  const source = await readEditorWorkflowSource(id, { readTrustedActiveHistory: reader })
  state().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  state().updateSpecField('title', 'Edited')
  return reader
}

describe('trusted history recovery to first draft', () => {
  it('independently verifies the original row and saves the complete user document', async () => {
    const reader = await open()
    expect(structuredClone(state()._activeBaseline)).toMatchObject({ specJson: archive, activeToken: token })
    expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null })
    expect(reader).not.toHaveBeenCalled() // content resolution requires Git evidence, not the optional ambiguity reader
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(`/api/workflows/${id}/pack-version?version=current`, expect.anything())
    expect(sent).toEqual([{ workflowId: id, packId, expectedDraftAbsent: true, expectedActiveDeployNumber: 3,
      expectedActiveToken: token, spec: { ...authored, title: 'Edited' } }])
    expect(draft).toEqual({ ...authored, title: 'Edited' })
    expect(state()._databaseSpec).toEqual(draft)
    expect(state().isDirty).toBe(false)
  })
  it.each(['token', 'content'] as const)('rejects Git history with mismatched %s without writing', async mismatch => {
    if (mismatch === 'token') gitToken = 'f'.repeat(64)
    else gitYaml = stringify({ ...authored, title: 'Other Git document' })
    await expect(readEditorWorkflowSource(id)).rejects.toThrow('未替换草稿')
    expect(sent).toHaveLength(0); expect(draft).toBeNull(); expect(state().spec).toBeNull()
  })
  it.each(['active', 'snapshot', 'pack', 'token'] as const)('does not POST when original %s changes', async kind => {
    await open()
    if (kind === 'active') currentActive = { ...active, deployNumber: 4 }
    if (kind === 'snapshot') currentSnapshot = { ...snapshot, specJson: JSON.stringify({ ...authored, content: 'changed' }) }
    if (kind === 'pack') currentActive = { ...active, packId: 'other' }
    if (kind === 'token') currentActive = { ...active, activeToken: 'f'.repeat(64) }
    await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('生效版本')
    expect(sent).toHaveLength(0); expect(state().isDirty).toBe(true)
  })
  it.each(['history', 'snapshot'] as const)('does not POST if another client creates a draft during %s preflight', async phase => {
    await open()
    if (phase === 'history') onHistory = () => { draft = { ...authored, title: 'Concurrent' } }
    else onSnapshot = () => { draft = { ...authored, title: 'Concurrent' } }
    await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('草稿状态')
    expect(sent).toHaveLength(0); expect(state().isDirty).toBe(true)
  })
  it('retains local edits when the active row changes after preflight and the server rejects it', async () => {
    await open()
    beforePost = () => { currentActive = { ...active, activeToken: 'f'.repeat(64) } }
    await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('保存冲突')
    expect(sent).toHaveLength(1); expect(draft).toBeNull()
    expect(state().spec?.title).toBe('Edited'); expect(state().isDirty).toBe(true)
  })
  it('retains local edits when a draft appears after preflight and server atomic check rejects it', async () => {
    await open()
    beforePost = () => { draft = { ...authored, title: 'Concurrent' } }
    await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('保存冲突')
    expect(sent).toHaveLength(1); expect(draft?.title).toBe('Concurrent')
    expect(state().spec?.title).toBe('Edited'); expect(state().isDirty).toBe(true)
  })
})
