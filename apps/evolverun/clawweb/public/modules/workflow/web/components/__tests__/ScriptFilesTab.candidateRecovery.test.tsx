import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ScriptFilesTab from '../ScriptFilesTab'
import { publish, sessionFor, updateVersion } from '../../editor/packVersionSession'
import { useEditorStore } from '../../editor/store'

const file = (text: string) => ({ path: 'script.txt', contentBase64: btoa(text), mode: '100644' as const })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('allows explicit recovery from two dirty versions with a dirty canvas without losing the other candidate', () => {
  const id = 'candidate-recovery-test', session = sessionFor(id)
  const state = (version: 'current' | 'draft') => ({ snapshot: { workflowId: id, packId: 'pack', baseRef: (version === 'current' ? `refs/tags/deploy/${id}/#1` : `origin/wf/${id}`), baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: [file('original')] }, files: [file(`${version} candidate`)], loading: false, error: null, blocked: false, notice: null })
  publish(session, { ...session.state, version: 'draft', versions: { current: state('current'), draft: state('draft') } })
  vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValue(true)
  render(<ScriptFilesTab workflowId={id} canvasDirty />)
  fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
  expect(session.state.version).toBe('draft')
  fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
  expect(session.state.version).toBe('current')
  expect(session.state.versions.draft.files).toEqual([file('draft candidate')])
  fireEvent.click(screen.getByRole('button', { name: /放弃本版本文件修改/ }))
  expect(session.state.versions.current.files).toEqual([file('current candidate')])
  fireEvent.click(screen.getByRole('button', { name: /放弃本版本文件修改/ }))
  expect(session.state.versions.current.files).toEqual([file('original')])
  expect(session.state.versions.draft.files).toEqual([file('draft candidate')])
})

it('exports the exact candidate before any discard', async () => {
  const id = 'candidate-export-test', session = sessionFor(id), blobs: Blob[] = []
  const snapshot = { workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`, baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: [file('base')] }
  publish(session, { ...session.state, versions: { ...session.state.versions, draft: { snapshot, files: [file('keep exact bytes')], loading: false, error: null, blocked: false, notice: null } } })
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: (blob: Blob) => { blobs.push(blob); return 'blob:candidate' }, revokeObjectURL: vi.fn() }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<ScriptFilesTab workflowId={id} canvasDirty />)
  fireEvent.click(screen.getByRole('button', { name: '导出本版本候选（JSON）' }))
  const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blobs[0]) })
  expect(JSON.parse(text)).toMatchObject({ workflowId: id, sourceVersion: 'draft', sourceCommit: snapshot.baseCommit, files: [file('keep exact bytes')] })
  expect(session.state.versions.draft.files).toEqual([file('keep exact bytes')])
})

it('does not discard either candidate when session state changes during confirmation', () => {
  const id = 'candidate-confirm-race-test', session = sessionFor(id)
  const state = (version: 'current' | 'draft') => ({ snapshot: { workflowId: id, packId: 'pack', baseRef: (version === 'current' ? `refs/tags/deploy/${id}/#1` : `origin/wf/${id}`), baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: [file('original')] }, files: [file(`${version} candidate`)], loading: false, error: null, blocked: false, notice: null })
  publish(session, { ...session.state, version: 'current', versions: { current: state('current'), draft: state('draft') } })
  useEditorStore.getState().reset({ force: true })
  useEditorStore.getState().loadSpec({ id, title: 'Original canvas', version: '1', nodes: [] })
  useEditorStore.getState().updateSpecField('title', 'Unsaved canvas')
  const confirm = vi.spyOn(window, 'confirm').mockImplementation(() => {
    updateVersion(session, 'draft', { files: [file('draft changed during confirmation')] })
    useEditorStore.getState().updateSpecField('title', 'Canvas changed during confirmation')
    return true
  })
  render(<ScriptFilesTab workflowId={id} canvasDirty />)
  fireEvent.click(screen.getByRole('button', { name: '放弃本版本文件修改' }))
  expect(confirm).toHaveBeenCalledOnce()
  expect(session.state.versions.current.files).toEqual([file('current candidate')])
  expect(session.state.versions.draft.files).toEqual([file('draft changed during confirmation')])
  expect(useEditorStore.getState().spec?.title).toBe('Canvas changed during confirmation')
  expect(useEditorStore.getState().isDirty).toBe(true)
})
