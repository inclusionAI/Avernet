import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesTab from '../ScriptFilesTab'
import { sessionFor } from '../../editor/packVersionSession'
import { usePackCanvasSession } from '../../editor/usePackCanvasSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
let sequence = 0
const makeId = () => `default-draft-${++sequence}`
const snapshot = (id: string, version: 'current' | 'draft') => ({
  workflowId: id, packId: 'pack', baseRef: (version === 'current' ? `refs/tags/deploy/${id}/#1` : `origin/wf/${id}`),
  baseCommit: (version === 'current' ? 'a' : 'b').repeat(40), draftHead: 'b'.repeat(40),
  versionFiles: [{ path: 'script.txt', contentBase64: btoa(version), mode: '100644' }],
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('opens a new session on wf draft without first reading current', async () => {
  const id = makeId()
  vi.mocked(fetchJson).mockResolvedValue(snapshot(id, 'draft'))
  render(<ScriptFilesTab workflowId={id} />)
  await screen.findByRole('button', { name: 'script.txt' })
  expect(screen.getByRole('button', { name: /草稿版/ })).toHaveAttribute('aria-pressed', 'true')
  expect(fetchJson).toHaveBeenCalledTimes(1)
  expect(fetchJson).toHaveBeenCalledWith(`/api/workflows/${id}/pack-version?version=draft`, expect.anything())
})

it.each([404, 403, 500, undefined])('keeps draft selected and shows the read failure %s without falling back', async status => {
  const id = makeId()
  vi.mocked(fetchJson).mockRejectedValue(Object.assign(new Error('failed'), { status }))
  render(<ScriptFilesTab workflowId={id} />)
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /草稿版/ })).toHaveAttribute('aria-pressed', 'true')
  expect(fetchJson).toHaveBeenCalledTimes(1)
  expect(fetchJson).toHaveBeenCalledWith(`/api/workflows/${id}/pack-version?version=draft`, expect.anything())
})

it('does not override an explicit current selection when the canvas hook initializes', async () => {
  const id = makeId()
  vi.mocked(fetchJson).mockImplementation(async url => snapshot(id, url.endsWith('version=current') ? 'current' : 'draft'))
  render(<ScriptFilesTab workflowId={id} />)
  await screen.findByRole('button', { name: 'script.txt' })
  fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
  await waitFor(() => expect(sessionFor(id).state.versions.current.snapshot).not.toBeNull())
  renderHook(() => usePackCanvasSession(id))
  expect(screen.getByRole('button', { name: /当前版/ })).toHaveAttribute('aria-pressed', 'true')
  expect(sessionFor(id).state.version).toBe('current')
})

it('retains dirty draft edits when the user explicitly switches versions', async () => {
  const id = makeId()
  vi.mocked(fetchJson).mockImplementation(async url => snapshot(id, url.endsWith('version=current') ? 'current' : 'draft'))
  render(<ScriptFilesTab workflowId={id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'script.txt' }))
  fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'local draft' } })
  fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
  await waitFor(() => expect(sessionFor(id).state.versions.current.snapshot).not.toBeNull())
  fireEvent.click(screen.getByRole('button', { name: /草稿版/ }))
  fireEvent.click(screen.getByRole('button', { name: 'script.txt' }))
  expect(screen.getByLabelText('文件内容')).toHaveValue('local draft')
})

it('does not override current when a late draft response arrives', async () => {
  const id = makeId()
  let resolveDraft!: (value: unknown) => void
  const draft = new Promise<unknown>(resolve => { resolveDraft = resolve })
  vi.mocked(fetchJson).mockImplementation(url => url.endsWith('version=draft') ? draft : Promise.resolve(snapshot(id, 'current')))
  render(<ScriptFilesTab workflowId={id} />)
  renderHook(() => usePackCanvasSession(id))
  fireEvent.click(screen.getByRole('button', { name: /当前版/ }))
  await waitFor(() => expect(sessionFor(id).state.versions.current.snapshot).not.toBeNull())
  await act(async () => { resolveDraft(snapshot(id, 'draft')); await draft })
  expect(sessionFor(id).state.version).toBe('current')
})

it('rejects a legacy saved source without exposing it as an editable wf draft', async () => {
  const id = makeId()
  vi.mocked(fetchJson).mockResolvedValue({ ...snapshot(id, 'draft'), baseRef: `origin/saved/${id}`, draftHead: null, canWrite: false })
  render(<ScriptFilesTab workflowId={id} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('无法读取完整版本文件')
  expect(screen.queryByRole('button', { name: 'script.txt' })).not.toBeInTheDocument()
  expect(sessionFor(id).state.versions.draft.snapshot).toBeNull()
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
