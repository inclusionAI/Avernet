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
  vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ signature: 'timeout', summary: { summary: 'Repeated timeout' }, sources: [{analysisContext:{workflowSnapshot:{status:'resolved',snapshot:{kind:'deployment',workflowId:'wf',workflowVersion:39,deployNumber:34,configDigest:'a'.repeat(64)}}}}] }], page: { page: 1, totalPages: 1 } })
  const snapshot = { kind: 'deployment', workflowId: 'wf', workflowVersion: 39, deployNumber: 34, configDigest: 'a'.repeat(64) }
  vi.mocked(repairBatches.candidates).mockResolvedValue({ items: [{ itemId: 'old', state: 'verified', sourceAvailable: true, sources: [], proposal: { summary: 'Preserve timeout bound' }, context: { sourceSnapshot: { status: 'resolved', snapshot } } }],
    activeSnapshot: { status: 'resolved', snapshot: { ...snapshot, workflowVersion: 40, deployNumber: 35 } }, canEdit: true, page: { page: 1, total: 1, totalPages: 1 } } as never)
  const onToggle = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { container, unmount } = render(<QueryClientProvider client={client}><HistoricalIssueArchive workflowId="wf" selected={[]} canEdit onToggle={onToggle} onResult={() => {}} renderItem={() => null} /></QueryClientProvider>)
  expect(readOnlyJson).not.toHaveBeenCalled()
  const outer = container.querySelector('details')!
  outer.open = true; fireEvent(outer, new Event('toggle'))
  await screen.findByText('工作流运行版本 39 · 部署 34 · 本页 1 个问题')
  expect(vi.mocked(readOnlyJson).mock.calls[0][0]).toContain('versionScope=historical')
  expect(repairBatches.candidates).not.toHaveBeenCalled()
  const version = container.querySelectorAll('details')[1]
  expect(version.textContent).toContain('工作流运行版本 39 · 部署 34')
  version.open = true; fireEvent(version, new Event('toggle'))
  expect(repairBatches.candidates).not.toHaveBeenCalled()
  await screen.findByText('Repeated timeout')
  const group = container.querySelectorAll('details')[2]
  group.open = true; fireEvent(group, new Event('toggle'))
  const checkbox = await screen.findByLabelText('选择 Preserve timeout bound')
  expect((checkbox as HTMLInputElement).disabled).toBe(false)
  expect(screen.getByText('工作流运行版本 39 · 部署 34 · 仅作参考目标')).toBeTruthy()
  fireEvent.click(checkbox)
  expect(onToggle).toHaveBeenCalledWith('old')
  expect(repairBatches.candidates).toHaveBeenCalledWith('wf', expect.objectContaining({ versionScope: 'historical', includeHistorical: true, snapshotKey: JSON.stringify(['deployment','wf',39,34]) }))
  unmount(); client.clear()
})

it('separates the same problem across deployment, saved-test and unknown versions without fetching collapsed suggestions',async()=>{
  const snapshot={kind:'deployment',workflowId:'wf',workflowVersion:39,deployNumber:34,configDigest:'a'.repeat(64)};
  const refs=[{status:'resolved',snapshot},{status:'resolved',snapshot:{...snapshot,workflowVersion:38,deployNumber:33}},
    {status:'resolved',snapshot:{kind:'saved',workflowId:'wf',commit:'b'.repeat(40),sourceFlowId:'test',configDigest:'c'.repeat(64)}},
    {status:'unresolved',reason:'LEGACY_MISSING'}];
  vi.mocked(readOnlyJson).mockResolvedValue({groups:[{signature:'timeout',sources:refs.map(workflowSnapshot=>({reasoning:'Timed out',analysisContext:{workflowSnapshot}}))}],page:{page:1,totalPages:1}});
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  const {container,unmount}=render(<QueryClientProvider client={client}><HistoricalIssueArchive workflowId="wf" selected={[]} canEdit onToggle={()=>{}} onResult={()=>{}} renderItem={()=>null}/></QueryClientProvider>);
  const outer=container.querySelector('details')!;outer.open=true;fireEvent(outer,new Event('toggle'));
  await screen.findByText('工作流运行版本 39 · 部署 34 · 本页 1 个问题');
  expect(screen.getByText('工作流运行版本 38 · 部署 33 · 本页 1 个问题')).toBeTruthy();
  expect(screen.getByText('保存测试 bbbbbbbb · 本页 1 个问题')).toBeTruthy();
  expect(screen.getByText('版本未确认 · 本页 1 个问题')).toBeTruthy();
  expect(repairBatches.candidates).not.toHaveBeenCalled();
  unmount();client.clear();
});
