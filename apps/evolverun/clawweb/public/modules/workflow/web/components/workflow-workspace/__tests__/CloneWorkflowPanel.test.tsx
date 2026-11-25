import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CloneWorkflowPanel from '../CloneWorkflowPanel'
import type { WorkflowCloneCandidate, WorkflowClonePreview } from '@avernet/clawweb-shared/web/api/client'

const candidates: WorkflowCloneCandidate[] = [
  { workflowId: 'shared-flow', title: 'Shared flow', packId: 'shared-pack', updatedAt: 1 },
]
const preview: WorkflowClonePreview = {
  sourceWorkflowId: 'shared-flow', sourcePackId: 'shared-pack', sourceTitle: 'Shared flow',
  baseCommit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', scriptCount: 2,
}

function renderPanel(onSubmit: ReturnType<typeof vi.fn>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CloneWorkflowPanel open isPending={false} onSubmit={onSubmit} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('CloneWorkflowPanel', () => {
  it('selects any saved workflow, previews its Git snapshot and submits a new identity', async () => {
    const onSubmit = vi.fn(async () => undefined)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/workflows/clone-candidates')) {
        return new Response(JSON.stringify({ data: candidates }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/api/workflows/clone/preview')) {
        return new Response(JSON.stringify(preview), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`unexpected fetch: ${url}`)
    }))
    renderPanel(onSubmit)

    await waitFor(() => expect(screen.getByText('Shared flow')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Shared flow'))
    await waitFor(() => expect(screen.getByText('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')).toBeInTheDocument())
    expect(screen.getByText('2')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/新工作流 ID/), { target: { value: 'my-copy' } })
    fireEvent.change(screen.getByLabelText(/新工作流标题/), { target: { value: 'My copy' } })
    fireEvent.click(screen.getByRole('button', { name: '基于源工作流创建' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({
      sourceWorkflowId: 'shared-flow', targetWorkflowId: 'my-copy', title: 'My copy', facade: undefined,
    }))
  })
})
