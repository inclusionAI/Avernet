import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import HistoricalIssueArchive from '../HistoricalIssueArchive'
import { readOnlyJson } from '../../../../api/read-only-json'
import { repairBatches } from '../../../../api/repair-batches'
vi.mock('../../../../api/read-only-json', () => ({ readOnlyJson: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: { candidates: vi.fn() } }))
afterEach(() => vi.clearAllMocks())
it('loads the folded history on demand and permits a verified historical goal without changing its lifecycle', async () => {
  vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ signature: 'timeout', summary: { summary: 'Repeated timeout' }, sources: [] }], page: { page: 1, totalPages: 1 } })
  const snapshot = { kind: 'deployment', workflowId: 'wf', workflowVersion: 39, deployNumber: 34, configDigest: 'a'.repeat(64) }
  vi.mocked(repairBatches.candidates).mockResolvedValue({ items: [{ itemId: 'old', state: 'verified', sourceAvailable: true, sources: [], proposal: { summary: 'Preserve timeout bound' }, context: { sourceSnapshot: { status: 'resolved', snapshot } } }],
    activeSnapshot: { status: 'resolved', snapshot: { ...snapshot, workflowVersion: 40, deployNumber: 35 } }, canEdit: true, page: { page: 1, total: 1, totalPages: 1 } } as never)
  const onToggle = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { container, unmount } = render(<QueryClientProvider client={client}><HistoricalIssueArchive workflowId="wf" selected={[]} canEdit onToggle={onToggle} onResult={() => {}} renderItem={() => null} /></QueryClientProvider>)
  expect(readOnlyJson).not.toHaveBeenCalled()
  const outer = container.querySelector('details')!
  outer.open = true; fireEvent(outer, new Event('toggle'))
  await waitFor(() => expect(screen.getByText('Repeated timeout')).toBeTruthy())
  expect(vi.mocked(readOnlyJson).mock.calls[0][0]).toContain('versionScope=historical')
  const group = container.querySelectorAll('details')[1]
  group.open = true; fireEvent(group, new Event('toggle'))
  const checkbox = await screen.findByLabelText('选择 Preserve timeout bound')
  expect((checkbox as HTMLInputElement).disabled).toBe(false)
  expect(screen.getByText('工作流运行版本 39 · 部署 34 · 仅作参考目标')).toBeTruthy()
  fireEvent.click(checkbox)
  expect(onToggle).toHaveBeenCalledWith('old')
  expect(repairBatches.candidates).toHaveBeenCalledWith('wf', expect.objectContaining({ versionScope: 'historical', includeHistorical: true }))
  unmount(); client.clear()
})
