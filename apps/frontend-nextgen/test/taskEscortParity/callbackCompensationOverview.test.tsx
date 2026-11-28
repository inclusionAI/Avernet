/** @jest-environment jsdom */
import { OverviewPage } from '@/pages/TaskEscort/parity/overview/OverviewPage';
import { callbackCompensationService } from '@/services/taskEscortParity/callbackCompensationService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { callbackCompensationCapabilities, callbackCompensationContext } from './callbackCompensationFixtures';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({ getRuntimeEnvironment: () => ({ status: 'available', value: 'PRE' }) }),
}));
jest.mock('@/services/taskEscortParity/callbackCompensationService', () => ({
  callbackCompensationService: { capabilities: jest.fn() },
}));
jest.mock('@/hooks/taskEscortParity/useTaskEscortOverview', () => ({
  useTaskEscortOverview: () => {
    const read = { data: null, loading: false, refresh: jest.fn() };
    return { runs: read, metrics: read, health: read, trend: read, nodes: read, refresh: jest.fn() };
  },
}));
beforeEach(() => {
  jest.clearAllMocks();
  (callbackCompensationService.capabilities as jest.Mock).mockResolvedValue(callbackCompensationCapabilities);
});
it('mounts the authorized entry to the left of refresh only in the selected workflow run-list tab', async () => {
  const view = render(<OverviewPage context={callbackCompensationContext} />);
  const button = await screen.findByRole('button', { name: '成功回调补偿' });
  expect(
    button.compareDocumentPosition(screen.getByRole('button', { name: '刷新' })) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: '节点分析' }));
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '运行列表' }));
  expect(screen.getByRole('button', { name: '成功回调补偿' })).toBeInTheDocument();
  expect(callbackCompensationService.capabilities).toHaveBeenCalledTimes(1);
  view.rerender(<OverviewPage context={{ ...callbackCompensationContext, workflowId: '', workflow: null }} />);
  expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument();
  expect(callbackCompensationService.capabilities).toHaveBeenCalledTimes(1);
});
