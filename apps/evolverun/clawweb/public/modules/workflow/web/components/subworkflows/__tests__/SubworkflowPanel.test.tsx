import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import NodeExecutionList from '../../NodeExecutionList';
import type { NodeExecution } from '@avernet/clawweb-shared/web/types';

function page(overrides = {}) {
  return { schemaVersion: 'fanout.v1', parentFlowId: 'parent', groupNodeId: 'group', groupExecutionId: 'generation',
    revision: 17, snapshotId: 'snapshot-17', status: 'succeeded', childWorkflowId: 'child-workflow', successPolicy: 'all_done',
    concurrency: 2, activeCount: 0, countScope: 'visible', groupTotal: null, visibleTotal: 2, returnedCount: 2, nextCursor: null,
    counts: { successCount: 1, failedCount: 1, pendingCount: 0, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0 },
    children: [{ displayIndex: 0, itemId: 'item-a', childFlowId: 'child-a', attemptNumber: 1, status: 'succeeded', error: null },
      { displayIndex: 1, itemId: 'item-b', childFlowId: 'child-b', attemptNumber: 1, status: 'failed',
        error: { category: 'execution', code: 'FAILED', message: 'Tool execution failed', retryable: false } }], ...overrides };
}
const node = { flow_id: 'parent', node_id: 'group', node_title: 'Experiment group', executor_type: 'subworkflow',
  status: 'succeeded', attempt: 1, started_at: 1, completed_at: 2 } as NodeExecution;
function view(selected = 'group', generation = 'generation') {
  return <NodeExecutionList nodes={[node]} flowId="parent" subworkflowGroups={[{ nodeId: 'group', groupExecutionId: generation }]}
    onSelectNode={() => {}} selectedNodeId={selected} onAnalyze={() => {}} />;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('group observation in the existing node detail panel', () => {
  it('shows authorized live counts, errors and opens child summary/nodes without losing parent context', async () => {
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      requests.push(url);
      if (url.includes('/subworkflows?')) return json(page());
      return json({ run: { flow_id: 'child-b', workflow_title: 'Child experiment', status: 'failed', node_count: 2 },
        nodes: [{ node_id: 'launch', node_title: 'Launch', status: 'failed' }, { node_id: 'watch', status: 'skipped' }] });
    }));
    render(view());
    expect(await screen.findByText('all_done')).toBeInTheDocument();
    expect(screen.getByText(/仅可见项统计/)).toBeInTheDocument();
    expect(screen.getByText(/item-a/)).toBeInTheDocument();
    expect(screen.getByText('Tool execution failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 child-b' }));
    const dialog = await screen.findByRole('dialog', { name: '子运行 child-b' });
    expect(await within(dialog).findByText('Child experiment')).toBeInTheDocument();
    expect(within(dialog).getByText('Launch')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: '打开完整运行' })).toHaveAttribute('href', '/runs/child-b');
    fireEvent.click(within(dialog).getByRole('button', { name: '返回父运行' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('Experiment group')).toBeInTheDocument();
    expect(requests).toContain('/api/runs/child-b');
  });
  it('does not request or poll a collapsed group', async () => {
    const fetch = vi.fn(async () => json(page())); vi.stubGlobal('fetch', fetch);
    render(view('')); await act(async () => {}); expect(fetch).not.toHaveBeenCalled();
  });
  it.each([[401, '需要登录'], [403, '无权查看'], [404, '暂无持久化数据'], [409, '执行代次或快照已变化'], [500, '读取失败']])(
    'distinguishes HTTP %s from an empty group', async (status, label) => {
      vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'read_error' }, status as number)));
      render(view()); expect(await screen.findByText(new RegExp(label as string))).toBeInTheDocument();
      expect(screen.queryByText('暂无可见子运行')).not.toBeInTheDocument();
    });
  it.each(['cancelled', 'failed'])('shows %s even when the authorized list is empty', async status => {
    vi.stubGlobal('fetch', vi.fn(async () => json(page({ status, children: [], visibleTotal: 0, returnedCount: 0 }))));
    render(view()); expect(await screen.findByText('暂无可见子运行')).toBeInTheDocument();
    expect(screen.getByTestId('fanout-status')).toHaveTextContent(status);
  });
  it('polls only while expanded, suppresses older revisions and clears data on revoked access', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetch = vi.fn().mockResolvedValueOnce(json(page()))
      .mockResolvedValueOnce(json(page({ revision: 16, status: 'running' })))
      .mockResolvedValueOnce(json({ error: 'forbidden' }, 403))
      .mockResolvedValueOnce(json(page({ revision: 16, status: 'running' })));
    vi.stubGlobal('fetch', fetch);
    const mounted = render(view());
    await waitFor(() => expect(screen.getByTestId('fanout-status')).toHaveTextContent('succeeded'));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.getByTestId('fanout-status')).toHaveTextContent('succeeded');
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.getByText(/无权查看/)).toBeInTheDocument();
    expect(screen.queryByText(/item-a/)).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.queryByText(/item-a/)).not.toBeInTheDocument();
    mounted.rerender(view('')); const calls = fetch.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); }); expect(fetch).toHaveBeenCalledTimes(calls);
  });
  it('shows the entry before the first durable group snapshot without inventing a generation', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<NodeExecutionList nodes={[node]} flowId="parent" onSelectNode={() => {}} selectedNodeId="group" onAnalyze={() => {}}
      workflowSpec={{ id: 'parent-workflow', nodes: [{ id: 'group', executor: { type: 'subworkflow', fanout: { concurrency: 2 } } }] } as never} />);
    expect(screen.getByText(/暂无持久化数据/)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('does not append a stale pagination response after a newer poll snapshot', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let completePage!: (value: Response) => void;
    const fetch = vi.fn().mockResolvedValueOnce(json(page({ nextCursor: 'cursor-17' })))
      .mockReturnValueOnce(new Promise<Response>(resolve => { completePage = resolve; }))
      .mockResolvedValueOnce(json(page({ revision: 18, snapshotId: 'snapshot-18', children: [], visibleTotal: 0, returnedCount: 0 })));
    vi.stubGlobal('fetch', fetch); render(view());
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.getByText('暂无可见子运行')).toBeInTheDocument();
    await act(async () => { completePage(json(page({ children: [{ ...page().children[0], itemId: 'stale-item' }] }))); });
    expect(screen.queryByText(/stale-item/)).not.toBeInTheDocument();
  });
  it('ignores a late response from a previously selected execution', async () => {
    let resolveOld!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce(json(page({ groupExecutionId: 'new-generation', revision: 1, children: [], visibleTotal: 0, returnedCount: 0 }))));
    const mounted = render(view()); mounted.rerender(view('group', 'new-generation'));
    expect(await screen.findByText('暂无可见子运行')).toBeInTheDocument();
    await act(async () => { resolveOld(json(page())); });
    expect(screen.queryByText(/item-a/)).not.toBeInTheDocument();
  });
});
