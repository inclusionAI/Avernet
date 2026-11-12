/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import AgentComputePage from '@/pages/AgentCompute';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { getCapabilities } from '@/capabilities';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn().mockResolvedValue(null),
  getRunDetail: jest.fn().mockResolvedValue(null),
  listRuns: jest.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
  submitRun: jest.fn(),
  cancelRun: jest.fn(),
}));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({ chatBots: [], isLoading: false, error: null, hasAgentCodingBots: false, reload: jest.fn() }),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: () => <textarea data-testid="yaml-code-editor" />,
}));

const capabilities = getCapabilities();

beforeEach(() => {
  useAgentComputePanelStore.getState().reset();
  jest
    .spyOn(capabilities, 'getHumanIdentity')
    .mockReturnValue({ status: 'available', value: { userId: '49912', displayName: 'dev', online: true } });
});

test('the route opens the panel for the current human, since there is no conversation', async () => {
  render(<AgentComputePage />);
  expect(await screen.findByRole('region', { name: '计算任务副屏' })).toBeInTheDocument();
  expect(useAgentComputePanelStore.getState().sessionId).toBe('49912');
});

test('without a resolvable user nothing renders rather than a broken panel', () => {
  jest.spyOn(capabilities, 'getHumanIdentity').mockReturnValue({ status: 'unsupported', reason: 'none' } as never);
  render(<AgentComputePage />);
  expect(screen.queryByRole('region', { name: '计算任务副屏' })).toBeNull();
});

test('the route renders the same panel the chat uses — no second layout', async () => {
  render(<AgentComputePage />);
  const panel = await screen.findByRole('region', { name: '计算任务副屏' });
  expect(panel).toHaveTextContent('计算任务');
  expect(screen.getByLabelText('收起计算任务副屏')).toBeInTheDocument();
});