import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ChildRunDrawer from '../ChildRunDrawer';

vi.mock('../../../api/run-subworkflows', () => ({
  readChildRun: async () => ({ run: { flow_id: 'child', workflow_id: 'child-wf', workflow_title: 'Child', status: 'running', node_count: 0 }, nodes: [] }),
  observationError: () => 'error',
}));
vi.mock('../../workflow-workspace/DetailDrawer', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
afterEach(cleanup);

it('labels the separately fetched child status as current', async () => {
  render(<ChildRunDrawer childFlowId="child" parentFlowId="parent" onClose={() => {}} />);
  expect(await screen.findByText(/子运行当前状态：/)).toBeInTheDocument();
});
