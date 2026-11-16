import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { useEditorPermissions } from '../useEditorPermissions'

const getAccess = vi.hoisted(() => vi.fn())
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { getAccess } } }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
function provider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, placeholderData: previous => previous } } })
  return { client, wrapper: ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider> }
}
describe('workflow editor verified capabilities', () => {
  it('does not fetch without a QueryClient and honors explicit legacy edit without granting execution', () => {
    const isolated = renderHook(() => useEditorPermissions('flow'))
    expect(isolated.result.current.canEdit).toBe(false)
    expect(isolated.result.current.canDeploy).toBe(false)
    expect(getAccess).not.toHaveBeenCalled()
    const legacy = renderHook(() => useEditorPermissions('flow', { canView: true, canEdit: true }))
    expect(legacy.result.current.canEdit).toBe(true)
    expect(legacy.result.current.canDeploy).toBe(false)
    const viewer = renderHook(() => useEditorPermissions('flow', { canView: true, canEdit: false }))
    expect(viewer.result.current.canEdit).toBe(false)
    expect(viewer.result.current.canDeploy).toBe(false)
    expect(getAccess).not.toHaveBeenCalled()
  })
  it.each([
    [true, false, true], [false, true, false], [undefined, true, false], [undefined, false, false],
  ])('uses explicit canDeploy=%s ahead of raw canExecute=%s', (canDeploy, canExecute, expected) => {
    const access = { canView: true, canEdit: true, canExecute, ...(canDeploy === undefined ? {} : { canDeploy }) }
    const hook = renderHook(() => useEditorPermissions('flow', access))
    expect(hook.result.current.canDeploy).toBe(expected)
  })
  it('fails closed while pending, after errors, and across workflows despite global placeholder defaults', async () => {
    const { wrapper, client } = provider()
    let resolve!: (value: unknown) => void
    getAccess.mockReturnValueOnce(new Promise(r => { resolve = r }))
    const hook = renderHook(({ id }) => useEditorPermissions(id), { initialProps: { id: 'a' }, wrapper })
    expect(hook.result.current).toMatchObject({ canEdit: false, canDeploy: false, pending: true })
    await act(async () => resolve({ canView: true, canEdit: true, canExecute: true }))
    await waitFor(() => expect(hook.result.current.canEdit).toBe(true))
    getAccess.mockRejectedValue(new Error('Unavailable'))
    hook.rerender({ id: 'b' })
    expect(hook.result.current.canEdit).toBe(false)
    expect(hook.result.current.canDeploy).toBe(false)
    await waitFor(() => expect(hook.result.current.pending).toBe(false))
    expect(hook.result.current.canEdit).toBe(false)
    cleanup(); client.clear()
  })
  it('keeps execute-only viewers from Editor deployment and leaves local creation available', async () => {
    const { wrapper, client } = provider()
    getAccess.mockResolvedValue({ canView: true, canEdit: false, canExecute: true })
    const hook = renderHook(({ id }) => useEditorPermissions(id), { initialProps: { id: 'flow' as string | null }, wrapper })
    await waitFor(() => expect(hook.result.current.pending).toBe(false))
    expect(hook.result.current.canDeploy).toBe(false)
    expect(hook.result.current.canEdit).toBe(false)
    hook.rerender({ id: null })
    expect(hook.result.current).toMatchObject({ canEdit: true, canDeploy: false })
    cleanup(); client.clear()
  })
})
