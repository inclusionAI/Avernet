import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SubworkflowPanel from '../SubworkflowPanel';

vi.mock('../../../hooks/useSubworkflows', () => ({
  useSubworkflows: () => ({ data: {
    status: 'cancelled', successPolicy: 'all_done', activeCount: 1, concurrency: 1, revision: 2,
    countScope: 'visible', groupTotal: null, visibleTotal: 1, nextCursor: null,
    counts: { successCount: 0, failedCount: 0, pendingCount: 1, cancelledCount: 0, timedOutCount: 0, rejectedCount: 0 },
    children: [{ displayIndex: 0, itemId: 'item', childFlowId: 'child', attemptNumber: 1, status: 'running', error: null }],
  }, loading: false, error: null, paging: false, loadMore: vi.fn(), retry: vi.fn() }),
}));
vi.mock('../ChildRunDrawer', () => ({ default: () => null }));
afterEach(cleanup);

it('labels child statuses and counts as a group snapshot rather than current child status', () => {
  render(<SubworkflowPanel flowId="parent" nodeId="node" groupExecutionId="group" />);
  expect(screen.getByText(/以下状态和统计来自组快照/)).toBeInTheDocument();
  expect(screen.getByText(/子运行可能继续执行/)).toBeInTheDocument();
});
