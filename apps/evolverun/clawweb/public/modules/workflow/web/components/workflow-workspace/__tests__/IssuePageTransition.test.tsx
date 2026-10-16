import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { expect, it, vi } from 'vitest'
const read = vi.hoisted(() => vi.fn())
vi.mock('../../../api/read-only-json', () => ({ readOnlyJson: read }))
import { useIssueGroups } from '../issue-groups'
function Page() {
  const [nodeId, setNode] = useState('old')
  const query = useIssueGroups('wf', { page: 1, pageSize: 20, nodeId })
  return <><button onClick={() => setNode('new')}>更换筛选</button><p>{query.data?.groups[0]?.signature ?? '空列表'}</p>
    {query.isPlaceholderData && <p role="status">正在更新筛选</p>}</>
}
it('keeps prior issue content while loading a new filter and replaces it when ready', async () => {
  let finish!: (result: unknown) => void
  read.mockResolvedValueOnce({ groups: [{ signature: '原问题' }] }).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Page /></QueryClientProvider>)
  await screen.findByText('原问题')
  await userEvent.click(screen.getByRole('button', { name: '更换筛选' }))
  expect(screen.getByText('原问题')).toBeVisible()
  expect(screen.getByRole('status')).toHaveTextContent('正在更新筛选')
  finish({ groups: [{ signature: '新筛选问题' }] })
  expect(await screen.findByText('新筛选问题')).toBeVisible()
})
