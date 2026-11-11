/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RunDetailPanel } from '@/components/agentswarm/RunDetailPanel';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { cancelRun } from '@/services/agentswarm/service';
import type { AgentDetailDTO, RunDetailDTO, RunSummaryDTO } from '@/services/agentswarm/types';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn(),
  getRunDetail: jest.fn(),
  listRuns: jest.fn(),
  cancelRun: jest.fn(),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));

const cancel = cancelRun as jest.Mock;

const run = (over: Partial<RunSummaryDTO> = {}): RunSummaryDTO => ({
  run_id: 'run-1',
  status: 'RUNNING',
  // epoch millis, as the gateway reports
  started_at: 1794365805609,
  ended_at: null,
  final_answer: null,
  error: null,
  termination_reason: null,
  ...over,
});

const agent = (over: Partial<AgentDetailDTO> = {}): AgentDetailDTO => ({
  agent_id: 'a1',
  name: 'greeter',
  role: 'greets',
  kind: 'bot',
  status: 'COMPLETED',
  depth: 0,
  spawn_round: 0,
  idempotency_key: 'k',
  attempts: 1,
  output: { text: 'hello world' },
  messages: [],
  ...over,
});

const detail = (agents: AgentDetailDTO[]): RunDetailDTO => ({
  run_id: 'run-1',
  status: 'COMPLETED',
  tenant: 'teamclaw',
  goal: 'g',
  provider: 'local',
  agents,
});

const props = (over: Partial<React.ComponentProps<typeof RunDetailPanel>> = {}) => ({
  tenant: 'teamclaw',
  run: run(),
  detail: null,
  loading: false,
  error: null,
  onReload: jest.fn(),
  onCancelled: jest.fn(),
  ...over,
});

beforeEach(() => cancel.mockReset());

test('a finished run shows its final answer', () => {
  render(<RunDetailPanel {...props({ run: run({ status: 'COMPLETED', final_answer: 'hello world' }) })} />);
  expect(screen.getByText('最终结果')).toBeInTheDocument();
  expect(screen.getByText(/hello world/)).toBeInTheDocument();
});

test('a running run is not given a final answer', () => {
  render(<RunDetailPanel {...props()} />);
  expect(screen.queryByText('最终结果')).toBeNull();
});

test('the agent section collapses and expands on click', () => {
  render(
    <RunDetailPanel
      {...props({ run: run({ status: 'COMPLETED' }), detail: detail([agent()]) })}
    />,
  );
  const toggle = screen.getByRole('button', { name: /执行 Agent/ });
  // A finished run starts expanded.
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('greeter')).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('greeter')).toBeNull();

  fireEvent.click(toggle);
  expect(screen.getByText('greeter')).toBeInTheDocument();
});

test('an active run starts collapsed so its progress stays in view', () => {
  render(<RunDetailPanel {...props({ run: run({ status: 'RUNNING' }), detail: detail([agent()]) })} />);
  expect(screen.getByRole('button', { name: /执行 Agent/ })).toHaveAttribute('aria-expanded', 'false');
});

test('each agent collapses independently', () => {
  render(
    <RunDetailPanel
      {...props({
        run: run({ status: 'COMPLETED' }),
        detail: detail([agent({ agent_id: 'a1', name: 'first' }), agent({ agent_id: 'a2', name: 'second' })]),
      })}
    />,
  );
  const firstRow = screen.getByText('first').closest('button')!;
  const secondRow = screen.getByText('second').closest('button')!;
  expect(firstRow).toHaveAttribute('aria-expanded', 'true');
  expect(secondRow).toHaveAttribute('aria-expanded', 'true');

  fireEvent.click(firstRow);
  // Collapsing one agent must not collapse the other.
  expect(firstRow).toHaveAttribute('aria-expanded', 'false');
  expect(secondRow).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('second')).toBeInTheDocument();
});

test('timestamps render as real dates from millisecond values', () => {
  render(<RunDetailPanel {...props()} />);
  expect(screen.getByText(/开始 \d{4}/)).toBeInTheDocument();
});

test('cancel reports the gateway status rather than assuming a stop', async () => {
  cancel.mockResolvedValue({ run_id: 'run-1', status: 'CANCELLED' });
  const onCancelled = jest.fn();
  render(<RunDetailPanel {...props({ onCancelled })} />);
  fireEvent.click(screen.getByText('取消运行'));
  await waitFor(() => expect(onCancelled).toHaveBeenCalled());
  const { notifySuccess } = jest.requireMock('@/components/ui/notify');
  expect(notifySuccess).toHaveBeenCalledWith(expect.stringContaining('CANCELLED'));
});

test('a finished run offers no cancel action', () => {
  render(<RunDetailPanel {...props({ run: run({ status: 'COMPLETED', final_answer: 'x' }) })} />);
  expect(screen.queryByText('取消运行')).toBeNull();
});

test('a refused cancel explains itself and keeps the run state', async () => {
  cancel.mockRejectedValue(new AgentswarmRequestError('already finished', 'refused', 409));
  const onCancelled = jest.fn();
  render(<RunDetailPanel {...props({ onCancelled })} />);
  fireEvent.click(screen.getByText('取消运行'));
  await waitFor(() => expect(cancel).toHaveBeenCalled());
  const { notifyError } = jest.requireMock('@/components/ui/notify');
  expect(notifyError).toHaveBeenCalledWith('already finished');
  expect(onCancelled).not.toHaveBeenCalled();
});

test('an empty agent list says so instead of rendering nothing', () => {
  render(<RunDetailPanel {...props({ run: run({ status: 'COMPLETED' }), detail: detail([]) })} />);
  expect(screen.getByText('暂无 Agent 详情。')).toBeInTheDocument();
});