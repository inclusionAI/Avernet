import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { sessionFor, updateVersion } from '../packVersionSession'
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
let sequence = 0
const snapshot = (id: string, version: 'current' | 'draft') => ({ workflowId: id, packId: 'pack', baseRef: (version !== 'draft' ? `refs/tags/deploy/${id}/#1` : `origin/wf/${id}`), baseCommit: (version === 'draft' ? 'b' : 'a').repeat(40), draftHead: 'b'.repeat(40), versionFiles: [] })
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('defaults to draft but does not load it until enabled', async () => {
  const id = `lazy-pack-${++sequence}`, session = sessionFor(id)
  vi.mocked(fetchJson).mockImplementation(async url => snapshot(id, url.endsWith('version=draft') ? 'draft' : 'current'))
  const view = renderHook(({ enabled }) => usePackCanvasSession(id, undefined, { enabled }), { initialProps: { enabled: false } })
  expect(fetchJson).not.toHaveBeenCalled()
  act(() => updateVersion(session, 'current', { snapshot: snapshot(id, 'current') }))
  expect(session.state.version).toBe('draft')
  view.rerender({ enabled: true })
  await waitFor(() => expect(session.state.version).toBe('draft'))
  await waitFor(() => expect(session.state.versions.draft.snapshot).not.toBeNull())
  expect(fetchJson).toHaveBeenCalledTimes(1)
  expect(fetchJson).toHaveBeenCalledWith(`/api/workflows/${id}/pack-version?version=draft`, expect.anything())
})
it('keeps automatic loading enabled by default for explicit Pack consumers', async () => {
  const id = `lazy-pack-${++sequence}`
  vi.mocked(fetchJson).mockResolvedValue(snapshot(id, 'draft'))
  renderHook(() => usePackCanvasSession(id))
  await waitFor(() => expect(sessionFor(id).state.versions.draft.snapshot).not.toBeNull())
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
