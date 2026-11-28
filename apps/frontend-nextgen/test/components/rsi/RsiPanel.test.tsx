/** @jest-environment jsdom */
import { RsiPanel } from '@/components/rsi/RsiPanel';
import * as api from '@/services/rsi/service';
import { rsiScopeKey, useRsiPanelStore } from '@/stores/rsiPanelStore';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useMediaQuery', () => ({ useMediaQuery: () => true }));
jest.mock('@/components/rsi/GenomeBaselinePanel', () => ({ GenomeBaselinePanel: () => null }));
jest.mock('@/components/rsi/RsiRunNavigation', () => ({
  RsiReviewQueue: () => null, RsiRunList: () => null,
}));
jest.mock('@/components/rsi/RsiRunDetails', () => ({ RsiRunDetails: () => null }));
jest.mock('@/services/rsi/service', () => ({
  getRsiBindings: jest.fn(), listRsiStrategies: jest.fn(), listRsiRuns: jest.fn(),
  getRsiReviewQueue: jest.fn(), submitRsiRun: jest.fn(), initializeGenomeBaseline: jest.fn(),
  getRsiEvidence: jest.fn(), getRsiCandidates: jest.fn(), putRsiBindings: jest.fn(),
}));
const scope = { botId: 'bot-a', ownerId: 'owner', actorId: 'actor' };
const binding: api.RsiBinding = {
  id: 'binding-a', strategy: 'strategy@1', trigger: { manual: true }, parent: 'active',
  allowed_genes: ['persona'], verification_profile: 'pending@1',
  budget: { max_usd: 0.1, max_wall_clock_s: 60 },
};
const failure = (status: number, code?: string) => Object.assign(new Error('private upstream detail'), {
  status, data: { error: { code, details: ['private validation detail'] } },
});
const pending = () => useRsiPanelStore.getState().attempts[rsiScopeKey(scope)];
async function openPanel() {
  const view = render(<RsiPanel scope={scope} enabled />);
  await waitFor(() => expect(screen.getByRole('button', { name: /启动RSI任务|重试同一次提交/ })
    .hasAttribute('disabled')).toBe(false));
  return view;
}
beforeEach(() => {
  jest.resetAllMocks();
  sessionStorage.clear();
  useRsiPanelStore.setState({ scope, attempts: {} });
  jest.mocked(api.getRsiBindings).mockResolvedValue({ bindings: [binding], etag: 'etag' });
  jest.mocked(api.listRsiStrategies).mockResolvedValue({ strategies: [] });
  jest.mocked(api.listRsiRuns).mockResolvedValue({ runs: [], total: 0 });
  jest.mocked(api.getRsiReviewQueue).mockResolvedValue({ items: [], total: 0 });
  jest.mocked(api.getRsiEvidence).mockResolvedValue({ jobs: [], events: [], next_cursor: 0 });
  jest.mocked(api.getRsiCandidates).mockResolvedValue({ candidates: [] });
});

test('definite binding rejection releases the matching attempt and configuration, without changing the Bot', async () => {
  jest.mocked(api.submitRsiRun).mockRejectedValue(failure(422, 'binding_check_failed'));
  await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '启动RSI任务' }));
  await screen.findByRole('alert');
  await waitFor(() => expect(pending()).toBeUndefined());
  expect(screen.getByRole('alert').textContent).toContain('绑定校验失败');
  expect(screen.getByRole('alert').textContent).not.toContain('private');
  expect(screen.getByLabelText('绑定配置（完整 JSON 数组）').hasAttribute('disabled')).toBe(false);
  expect(screen.getByRole('button', { name: '重新读取配置' }).hasAttribute('disabled')).toBe(false);
  expect(api.putRsiBindings).not.toHaveBeenCalled();
  expect(api.initializeGenomeBaseline).not.toHaveBeenCalled();
});

test.each([
  ['network', new TypeError('Failed to fetch')],
  ['server', failure(500)],
  ['unclassified 422', failure(422)],
  ['different domain', failure(422, 'other_failure')],
  ['inconsistent status', failure(503, 'binding_check_failed')],
])('%s failure retains the same key/body across reopen and retry', async (_name, cause) => {
  jest.mocked(api.submitRsiRun).mockRejectedValue(cause);
  const view = await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '启动RSI任务' }));
  await screen.findByRole('alert');
  const attempt = pending();
  expect(attempt).toBeDefined();
  expect(screen.getByLabelText('绑定配置（完整 JSON 数组）').hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('alert').textContent).not.toContain('private');
  view.unmount();
  await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '重试同一次提交' }));
  await screen.findByRole('alert');
  expect(pending()).toEqual(attempt);
  expect(api.submitRsiRun).toHaveBeenNthCalledWith(1, scope, binding.id, attempt!.key);
  expect(api.submitRsiRun).toHaveBeenNthCalledWith(2, scope, binding.id, attempt!.key);
});

test('a restored uncertain attempt is released only after a definite binding rejection', async () => {
  const original = useRsiPanelStore.getState().beginAttempt(scope, binding.id);
  // Simulate the sessionStorage hydration that occurs after a page reload.
  useRsiPanelStore.setState({ attempts: {} });
  sessionStorage.setItem('avernet-rsi-submissions-v1', JSON.stringify({
    state: { attempts: { [rsiScopeKey(scope)]: original } }, version: 0,
  }));
  await useRsiPanelStore.persist.rehydrate();
  jest.mocked(api.submitRsiRun).mockRejectedValue(failure(422, 'binding_check_failed'));
  await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '重试同一次提交' }));
  await waitFor(() => expect(pending()).toBeUndefined());
  expect(api.submitRsiRun).toHaveBeenCalledWith(scope, binding.id, original.key);
  expect(screen.getByLabelText('运行绑定').hasAttribute('disabled')).toBe(false);
});

test('baseline initialization still retries exactly once with the original key', async () => {
  jest.mocked(api.submitRsiRun).mockRejectedValueOnce(failure(409, 'no_genome_baseline'))
    .mockResolvedValueOnce({ run: 'run-a' });
  jest.mocked(api.initializeGenomeBaseline).mockResolvedValue({ revision: 'sha256:base', created: true });
  await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '启动RSI任务' }));
  await waitFor(() => expect(api.submitRsiRun).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(pending()).toBeUndefined());
  expect(jest.mocked(api.submitRsiRun).mock.calls[0]).toEqual(jest.mocked(api.submitRsiRun).mock.calls[1]);
  expect(api.initializeGenomeBaseline).toHaveBeenCalledTimes(1);
});

test('a late rejection cannot clear a replacement attempt', async () => {
  let reject!: (cause: unknown) => void;
  jest.mocked(api.submitRsiRun).mockImplementation(() => new Promise((_resolve, rejectPromise) => {
    reject = rejectPromise;
  }));
  await openPanel();
  fireEvent.click(screen.getByRole('button', { name: '启动RSI任务' }));
  const replacement = { key: 'rsi-replacement', bindingId: 'other-binding' };
  act(() => useRsiPanelStore.setState({ attempts: { [rsiScopeKey(scope)]: replacement } }));
  await act(async () => reject(failure(422, 'binding_check_failed')));
  expect(pending()).toEqual(replacement);
});
