/** @jest-environment jsdom */
/**
 * The run log's ordering.
 *
 * The log is rebuilt from a polled payload, so the same input must always
 * produce the same list. An order that depends on arrival order reshuffles on
 * refresh, which reads as the run having done something different.
 */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import AgentComputeRunPage from '@/pages/AgentCompute/Run';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useParams: () => ({ runId: 'run-1' }),
}));

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn().mockResolvedValue(null),
  getRunDetail: jest.fn(),
  listRuns: jest.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 }),
  cancelRun: jest.fn(),
  submitRun: jest.fn(),
  listDefinitions: jest.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 9 }),
  createDefinition: jest.fn(),
  updateDefinition: jest.fn(),
  deleteDefinition: jest.fn(),
  getDefinition: jest.fn(),
}));

jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({ chatBots: [], isLoading: false, error: null, hasAgentCodingBots: false, reload: jest.fn() }),
}));

const getDetail = () => jest.requireMock('@/services/agentswarm/service').getRunDetail as jest.Mock;

/** Three agents in one round whose attempts share a millisecond. */
function detail(agents: Array<{ name: string; startedAt: number }>) {
  return {
    run_id: 'run-1', row_id: 1, status: 'COMPLETED', goal: 'g', provider: 'default',
    started_at: 1795771900000, ended_at: 1795771960000,
    final_answer: 'done',
    // The round row is written AFTER its first attempts, as the gateway does.
    rounds: [{ round_id: 'r0', stage_index: 0, orchestrator_steps: 1,
      slowest_attempt_steps: 1, cohort_agent_ids: agents.map((a) => a.name),
      started_at: 1795771903859 }],
    agent_catalog: agents.map((a) => ({ name: a.name, role: '', kind: 'llm' })),
    agents: agents.map((a) => ({
      agent_id: `id-${a.name}`, name: a.name, catalog_name: a.name, role: '',
      kind: 'llm', status: 'COMPLETED', depth: 0, spawn_round: 0,
      idempotency_key: `k-${a.name}`, attempts: 1, output: { text: a.name },
      objective: `产出 ${a.name}`,
      attempt_ledger: [{ claim_epoch: 1, slice_retry_index: 1, executor_kind: 'llm',
        executor_id: `e-${a.name}`, executor_name: a.name, binding_state: 'bound',
        result_status: 'COMPLETED', started_at: a.startedAt }],
    })),
    guards: [],
  };
}

async function openLog() {
  render(<AgentComputeRunPage />);
  fireEvent.click(await screen.findByRole('button', { name: '运行记录' }));
  return screen.findByLabelText('运行记录');
}

/**
 * The attempt rows only.
 *
 * A round is now a collapsible group whose header is itself a list item; reading
 * every item would count the round header as an agent line.
 */
function labels(log: HTMLElement): string[] {
  return within(log)
    .getAllByRole('listitem')
    .filter((item) => /第 \d+ 次尝试/.test(item.textContent ?? ''))
    .map((item) => item.textContent ?? '');
}

describe('运行记录的排序', () => {
  test('同一轮内时间相同时按 Agent 名称排序', async () => {
    // Same millisecond for all three: only the name can order them. Delivered
    // in reverse-name order so the input cannot be what fixes the sequence.
    getDetail().mockResolvedValue(detail([
      { name: 'world_writer', startedAt: 1795771903855 },
      { name: 'hello_writer', startedAt: 1795771903855 },
      { name: 'brave_writer', startedAt: 1795771903855 },
    ]));
    const log = await openLog();

    const ordered = labels(log);
    // Read the names in the order the DOM presents them — asserting against a
    // fixed array would pass whatever the actual order was.
    const names = ordered
      .map((line) => /^[\d:\- ]*([a-z]+_writer)/.exec(line.trim())?.[1])
      .filter(Boolean);
    expect(names).toEqual(['brave_writer', 'hello_writer', 'world_writer']);
  });

  test('同一份数据重复渲染顺序不变', async () => {
    const input = [
      { name: 'b_writer', startedAt: 1795771903855 },
      { name: 'a_writer', startedAt: 1795771903855 },
      { name: 'c_writer', startedAt: 1795771903855 },
    ];
    getDetail().mockResolvedValue(detail(input));
    const first = labels(await openLog());

    // The poll delivers the same rows in a different array order; the log must
    // not follow it.
    getDetail().mockResolvedValue(detail([input[2], input[0], input[1]]));
    fireEvent.click(screen.getByRole('button', { name: '执行详情' }));
    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    const second = labels(await screen.findByLabelText('运行记录'));

    expect(second).toEqual(first);
  });
});
