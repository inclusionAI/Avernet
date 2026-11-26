/** @jest-environment jsdom */
import { RsiStrategyOutputPanel } from '@/components/rsi/RsiStrategyOutput';
import { getRsiStrategyOutput, type RsiRun, type RsiStrategyOutput } from '@/services/rsi/service';
import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/services/rsi/service', () => ({
  getRsiStrategyOutput: jest.fn(),
  isRsiTerminal: (status: string) => ['completed', 'failed', 'cancelled', 'budget_exhausted'].includes(status),
}));
jest.mock('@/services/rsi/errors', () => ({ rsiErrorMessage: (_cause: unknown, fallback: string) => fallback }));
const getOutput = jest.mocked(getRsiStrategyOutput);
const scope = { botId: 'bot-a', ownerId: 'owner-a', actorId: 'actor' };
const run: RsiRun = {
  id: 'run-a',
  binding_id: 'binding',
  strategy: 'behavior@1',
  status: 'completed',
  attempt: 1,
  parent_revision: 'parent',
  candidate_count: 0,
  created_at: '',
  end_reason: null,
  summary: null,
  budget_used: {},
};
const output: RsiStrategyOutput = {
  schema_version: 1,
  attempt: 1,
  outcome: 'no_changes',
  reason: 'empty_edits',
  rationale: '证据不足，不建议修改',
  edits: [],
  raw_output: '<img src=x onerror="alert(1)">',
  raw_output_truncated: false,
};
beforeEach(() => jest.clearAllMocks());

test('shows no-change rationale and escapes final response as plain text', async () => {
  getOutput.mockResolvedValue({ output });
  const view = render(<RsiStrategyOutputPanel scope={scope} run={run} />);
  await screen.findByText(output.rationale);
  expect(screen.getByText('模型返回了空 edits 列表。')).toBeTruthy();
  expect(screen.getByText(output.raw_output)).toBeTruthy();
  expect(view.container.querySelector('img')).toBeNull();
  expect(getOutput).toHaveBeenCalledWith(scope, run.id);
});

test('missing historical output is not described as a model no-change decision', async () => {
  getOutput.mockResolvedValue({ output: null });
  render(<RsiStrategyOutputPanel scope={scope} run={run} />);
  await screen.findByText(/未保存策略模型输出，无法回溯/);
  expect(screen.queryByText('模型返回了空 edits 列表。')).toBeNull();
});

test('read errors remain distinct from missing output and can be retried', async () => {
  getOutput.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({ output });
  render(<RsiStrategyOutputPanel scope={scope} run={run} />);
  await screen.findByRole('alert');
  expect(screen.queryByText(/无法回溯/)).toBeNull();
  fireEvent.click(screen.getByText('重试读取策略输出'));
  await screen.findByText(output.rationale);
  expect(screen.queryByRole('alert')).toBeNull();
});

test('shows changed files and marks truncated invalid final response', async () => {
  getOutput.mockResolvedValue({
    output: {
      ...output,
      outcome: 'invalid_proposal',
      reason: 'invalid_model_proposal',
      edits: [{ path: 'skills/local/SKILL.md', content: '# Proposed text', changed: true }],
      raw_output_truncated: true,
    },
  });
  render(<RsiStrategyOutputPanel scope={scope} run={run} />);
  await screen.findByText('skills/local/SKILL.md（建议修改）');
  expect(screen.getByText('# Proposed text')).toBeTruthy();
  expect(screen.getByText(/已脱敏，已截断/)).toBeTruthy();
  expect(screen.getByText(/模型输出未通过提案校验/)).toBeTruthy();
});

test('late previous Bot/Run results are ignored after keyed remount', async () => {
  let resolveOld!: (value: { output: RsiStrategyOutput }) => void;
  getOutput
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce({ output: { ...output, rationale: '当前 Bot 的建议' } });
  const view = render(<RsiStrategyOutputPanel key="old" scope={scope} run={run} />);
  view.rerender(
    <RsiStrategyOutputPanel key="new" scope={{ ...scope, botId: 'bot-b' }} run={{ ...run, id: 'run-b' }} />,
  );
  await screen.findByText('当前 Bot 的建议');
  await act(async () => {
    resolveOld({ output: { ...output, rationale: '旧 Bot 的私密输出' } });
  });
  expect(screen.queryByText('旧 Bot 的私密输出')).toBeNull();
});

test('running output refreshes on terminal transition and stops periodic reads', async () => {
  jest.useFakeTimers();
  try {
    getOutput.mockResolvedValueOnce({ output: null }).mockResolvedValue({ output });
    const view = render(<RsiStrategyOutputPanel scope={scope} run={{ ...run, status: 'running' }} />);
    await act(async () => {});
    expect(screen.getByText(/本次执行暂未保存/)).toBeTruthy();
    view.rerender(<RsiStrategyOutputPanel scope={scope} run={run} />);
    await act(async () => {});
    expect(screen.getByText(output.rationale)).toBeTruthy();
    const count = getOutput.mock.calls.length;
    await act(async () => {
      jest.advanceTimersByTime(9000);
    });
    expect(getOutput).toHaveBeenCalledTimes(count);
  } finally {
    jest.useRealTimers();
  }
});
