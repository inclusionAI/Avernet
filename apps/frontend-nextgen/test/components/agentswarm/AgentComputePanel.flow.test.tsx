/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AgentComputePanel } from '@/components/agentswarm/AgentComputePanel';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { getRunDetail, listRuns } from '@/services/agentswarm/service';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn().mockResolvedValue(null),
  getRunDetail: jest.fn(),
  listRuns: jest.fn(),
  submitRun: jest.fn(),
  cancelRun: jest.fn(),
}));
// The identity picker lists selectable Bots; give it one so a seeded default has
// somewhere to render.
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({
    chatBots: [
      { botId: 'seed_bot', realBotId: 'seed_bot', displayName: '示例 Bot', online: true, chatable: true, status: 'ACTIVE' },
    ],
    isLoading: false,
    error: null,
    hasAgentCodingBots: false,
    reload: jest.fn(),
  }),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: () => <textarea data-testid="yaml-code-editor" />,
}));

const list = listRuns as jest.Mock;
const detail = getRunDetail as jest.Mock;

const done = {
  run_id: 'run-done',
  status: 'COMPLETED',
  goal_excerpt: 'Say hello world',
  final_answer: 'hello world',
  started_at: 1794365805609,
  ended_at: 1794365945661,
  termination_reason: 'finish',
  error: null,
};
const live = {
  run_id: 'run-live',
  status: 'RUNNING',
  final_answer: null,
  started_at: 1794365805609,
  ended_at: null,
  termination_reason: null,
  error: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  useAgentComputePanelStore.getState().reset();
  list.mockResolvedValue({ items: [live, done], total: 2, page: 1, page_size: 20 });
  detail.mockResolvedValue({
    run_id: 'run-done',
    status: 'COMPLETED',
    tenant: 'teamclaw',
    goal: 'Say hello world',
    provider: 'local',
    final_answer: 'hello world',
    agents: [
      {
        agent_id: 'a1',
        name: 'greeter',
        role: 'greets',
        kind: 'bot',
        status: 'COMPLETED',
        depth: 0,
        spawn_round: 0,
        idempotency_key: 'k',
        attempts: 1,
        output: { text: 'Hello world' },
        messages: [],
      },
    ],
  });
});

test('a just-submitted run appears at the top before the list refresh catches up', async () => {
  const { getRun } = jest.requireMock('@/services/agentswarm/service');
  // The list is still empty; only the poll knows about the new run.
  list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
  getRun.mockResolvedValue({
    run_id: 'run-new',
    status: 'RUNNING',
    final_answer: null,
    started_at: 1794365805609,
    ended_at: null,
    termination_reason: null,
    error: null,
  });
  useAgentComputePanelStore.getState().openDetail('s1', 'run-new');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);

  await waitFor(() => expect(screen.getAllByText('run-new').length).toBeGreaterThan(0));
  expect(screen.queryByText('当前租户还没有运行记录。')).toBeNull();
});

test("the conversation's own Bot is preselected as the identity source", async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" defaultIdentityBotId="20261110_chatbot" />);
  await waitFor(() => expect(screen.getAllByText('20261110_chatbot').length).toBeGreaterThan(0));
});

test('a Bot that resolves after mount still becomes the default', async () => {
  useAgentComputePanelStore.getState().open('s1');
  const { rerender } = render(<AgentComputePanel sessionId="s1" userId="u1" defaultIdentityBotId={null} />);
  rerender(<AgentComputePanel sessionId="s1" userId="u1" defaultIdentityBotId="seed_bot" />);
  // The seeded value becomes the current choice rather than leaving the picker blank.
  await waitFor(() => expect(screen.queryByText('选择用于继承身份的运行 Bot')).toBeNull());
});

test('a Bot outside the fetched list still renders as the current choice', async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" defaultIdentityBotId="outside_bot" />);
  // Radix only labels options it knows, so the picker renders the value itself.
  await waitFor(() => expect(screen.queryByText('选择用于继承身份的运行 Bot')).toBeNull());
});

test('the form starts empty — an example is opted into, not shown by default', async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getByText(/Agent 定义 · 1/)).toBeInTheDocument());
  expect(screen.getByLabelText(/计算目标/)).toHaveValue('');
});

test('choosing an example fills the goal and its agents', async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  fireEvent.click(await screen.findByRole('button', { name: /填入示例任务/ }));
  await waitFor(() => expect(screen.getByText(/Agent 定义 · 3/)).toBeInTheDocument());
  expect((screen.getByLabelText(/计算目标/) as HTMLTextAreaElement).value).toContain('hello brave world');
});

test('clicking an already-expanded run collapses it again', async () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-done');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getByText('最终结果')).toBeInTheDocument());

  fireEvent.click(screen.getAllByText('run-done')[0]);
  await waitFor(() => expect(useAgentComputePanelStore.getState().runId).toBeUndefined());
  // Staying on the list, with the detail folded away.
  await waitFor(() => expect(screen.queryByText('最终结果')).toBeNull());
});

test('selecting a run expands its detail right under that row', async () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-done');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getByText('最终结果')).toBeInTheDocument());
  expect(screen.getByText('greeter')).toBeInTheDocument();
});

test('the goal of an opened run labels its row', async () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-done');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getAllByText('Say hello world').length).toBeGreaterThan(0));
});

test("the list shows each run's goal prefix from the gateway", async () => {
  useAgentComputePanelStore.getState().showDetail('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  // No extra detail request is needed: GET /runs carries goal_excerpt.
  await waitFor(() => expect(screen.getAllByText('Say hello world').length).toBeGreaterThan(0));
});

test('a run whose goal is empty keeps a placeholder rather than a blank row', async () => {
  list.mockResolvedValue({
    items: [{ ...done, goal_excerpt: '' }],
    total: 1,
    page: 1,
    page_size: 20,
  });
  useAgentComputePanelStore.getState().showDetail('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getAllByText('未命名运行').length).toBeGreaterThan(0));
});

test('the header offers a manual entry into the run list', async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  const entry = await screen.findByRole('button', { name: /运行详情/ });
  fireEvent.click(entry);
  expect(useAgentComputePanelStore.getState().view).toBe('detail');
});

test('the manual entry does not require a submit first', async () => {
  useAgentComputePanelStore.getState().open('s1');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(list).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: /运行详情/ }));
  await waitFor(() => expect(screen.getByText('运行记录')).toBeInTheDocument());
});

test('a running run shows no final answer and can be cancelled', async () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-live');
  render(<AgentComputePanel sessionId="s1" userId="u1" />);
  await waitFor(() => expect(screen.getAllByText('run-live').length).toBeGreaterThan(0));
  expect(screen.queryByText('最终结果')).toBeNull();
  expect(screen.getByText('取消运行')).toBeInTheDocument();
});