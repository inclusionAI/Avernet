import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { it, expect, vi } from 'vitest';
const state = vi.hoisted(() => ({ data: { groups: [] as any[] }, isLoading: false, isError: false }));
vi.mock('../issue-groups', () => ({ useIssueGroups: () => state }));
import AggregationProgress from '../AggregationProgress';
it('shows independent queued and failed status while retaining the old conclusion notice', () => {
  state.data.groups = [{ signature: 'timeout', flowIds: ['run'], aggregationStatus: 'queued', summary: null }];
  const view = () => <MemoryRouter><AggregationProgress workflowId="wf" flowId="run" /></MemoryRouter>;
  const { rerender } = render(view());
  expect(screen.getByText(/timeout：正在生成/)).toBeTruthy();
  state.data.groups[0] = { ...state.data.groups[0], aggregationStatus: 'failed', stale: true, summary: { summary: 'old' } };
  rerender(view());
  expect(screen.getByText(/timeout：聚合更新失败/)).toBeTruthy();
  expect(screen.getByText(/保留上次有效结论/)).toBeTruthy();
  state.isError = true;
  rerender(view());
  expect(screen.getByRole('alert')).toBeTruthy();
});
