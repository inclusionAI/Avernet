/** @jest-environment jsdom */
import { RsiManualPromotion } from '@/components/rsi/RsiManualPromotion';
import * as api from '@/services/rsi/service';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/rsi/service', () => ({
  ...jest.requireActual('@/services/rsi/service'),
  authorizeRsiPromotion: jest.fn(), applyRsiCandidate: jest.fn(), getRsiCandidateReport: jest.fn(),
}));
const authorize = jest.mocked(api.authorizeRsiPromotion);
const apply = jest.mocked(api.applyRsiCandidate);
const read = jest.mocked(api.getRsiCandidateReport);
const scope = { botId: 'bot-a', ownerId: 'owner', actorId: 'actor' };
const report: api.RsiCandidateReport = {
  candidate_id: 'candidate', revision_id: 'sha256:target', parent_revision_id: 'sha256:parent', run_id: 'run1',
  revision_seq: 2, parent_revision_seq: 1, strategy: 'skill', strategy_version: '1', patch_summary: [],
  diff_path: '', rationale: '', evidence: [], verdict: 'inconclusive', splits: [], cost: {}, flags: [],
  gate: null, review: null, manual_promotion: { authorization: null, promotion: null },
};
const auth: api.RsiHumanAuthorization = {
  id: 'a'.repeat(32), basis: 'human', candidate_id: report.candidate_id, revision_id: report.revision_id,
  parent_revision_id: report.parent_revision_id, run_id: report.run_id, strategy: 'skill@1',
  actor: 'human:actor', user_id: 'actor', reason: 'inspected diff', authorized_at: '2026-11-26',
  checks_at_authorization: { verdict: 'inconclusive', floor: 'not_run', floor_problems: ['no scanner'] },
};
const promotion: api.GenomePromotion = {
  id: auth.id, revision: auth.revision_id, from_revision: auth.parent_revision_id, status: 'applying',
  apply_id: 'apply1', apply_detail: '', reason: auth.reason, created_at: '',
};
const authorized = { ...report, manual_promotion: { authorization: auth, promotion: null } };
beforeEach(() => {
  jest.clearAllMocks();
  authorize.mockResolvedValue({ authorization: auth });
  apply.mockResolvedValue({ authorization: auth, promotion });
  read.mockResolvedValue(authorized);
});
function confirmAuthorization() {
  fireEvent.change(screen.getByLabelText('人工晋升理由（必填）'), { target: { value: 'inspected diff' } });
  fireEvent.click(screen.getByRole('checkbox'));
}

test('read-only by default; authorization and real Bot Apply require separate explicit confirmations', async () => {
  render(<RsiManualPromotion scope={scope} report={report} />);
  expect(authorize).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled();
  expect((screen.getByText('人工授权晋升（不 Apply）') as HTMLButtonElement).disabled).toBe(true);
  confirmAuthorization(); fireEvent.click(screen.getByText('人工授权晋升（不 Apply）'));
  await screen.findByText('Apply 到真实 Bot');
  expect(authorize).toHaveBeenCalledWith(scope, report.candidate_id, expect.any(String), {
    revision_id: report.revision_id, parent_revision_id: report.parent_revision_id, reason: 'inspected diff',
  });
  expect(apply).not.toHaveBeenCalled();
  expect((screen.getByText('Apply 到真实 Bot') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByText('Apply 到真实 Bot'));
  await screen.findByText(/交付 .* applying/);
  expect(apply).toHaveBeenCalledWith(scope, report.candidate_id, auth.id);
  expect(screen.getByText(/原评测结论保留为 inconclusive/)).toBeTruthy();
});

test('lost authorization response freezes key and body; original request retry does not Apply', async () => {
  authorize.mockRejectedValueOnce(new Error('timeout'));
  render(<RsiManualPromotion scope={scope} report={report} />);
  confirmAuthorization(); fireEvent.click(screen.getByText('人工授权晋升（不 Apply）'));
  await screen.findByRole('alert');
  expect((screen.getByLabelText('人工晋升理由（必填）') as HTMLTextAreaElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('使用原请求重试授权'));
  await screen.findByText('Apply 到真实 Bot');
  expect(authorize.mock.calls[1]).toEqual(authorize.mock.calls[0]);
  expect(apply).not.toHaveBeenCalled();
});

test('remount recovers authoritative authorization without another POST', async () => {
  const view = render(<RsiManualPromotion scope={scope} report={report} />);
  fireEvent.click(screen.getByText('刷新授权与交付状态'));
  await screen.findByText('Apply 到真实 Bot');
  view.unmount();
  render(<RsiManualPromotion scope={scope} report={authorized} />);
  expect(screen.getByText(/授权依据：human/)).toBeTruthy();
  expect(authorize).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled();
});

test.each(['applied_partial', 'apply_failed', 'applied'] as const)('shows honest %s settlement, never changes verdict', async (status) => {
  render(<RsiManualPromotion scope={scope} report={{ ...authorized, manual_promotion: { authorization: auth, promotion: { ...promotion, status } } }} />);
  expect(screen.getByRole('status').textContent).toContain(status);
  expect(screen.getByText(/原评测结论保留为 inconclusive/)).toBeTruthy();
  expect(screen.queryByText('Apply 到真实 Bot')).toBeNull();
  expect(!!screen.queryByText('人工授权晋升（不 Apply）')).toBe(status === 'apply_failed');
  expect(apply).not.toHaveBeenCalled();
});

test('switching Bot while authorization is pending cannot enable an old-scope Apply', async () => {
  let resolve!: (value: api.RsiManualPromotion) => void;
  authorize.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
  const view = render(<RsiManualPromotion scope={scope} report={report} />);
  confirmAuthorization(); fireEvent.click(screen.getByText('人工授权晋升（不 Apply）'));
  await waitFor(() => expect(authorize).toHaveBeenCalledTimes(1));
  view.rerender(<RsiManualPromotion scope={{ ...scope, botId: 'bot-b' }} report={report} />);
  await act(async () => resolve({ authorization: auth }));
  expect(screen.queryByText('Apply 到真实 Bot')).toBeNull();
  expect(apply).not.toHaveBeenCalled();
});

test('known rejection blocks authorization; missing capability stays disabled', () => {
  const view = render(<RsiManualPromotion scope={scope} report={{ ...report, verdict: 'reject' }} />);
  confirmAuthorization();
  expect((screen.getByText('人工授权晋升（不 Apply）') as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<RsiManualPromotion scope={scope} report={{ ...report, manual_promotion: null }} />);
  expect(screen.getByText(/当前部署未启用/)).toBeTruthy();
  expect(authorize).not.toHaveBeenCalled();
});

test('lost Apply response recovers using the same authorization, never a second authorization', async () => {
  apply.mockRejectedValueOnce(new Error('timeout'));
  render(<RsiManualPromotion scope={scope} report={authorized} />);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByText('Apply 到真实 Bot'));
  await screen.findByRole('alert');
  read.mockResolvedValue({ ...authorized, manual_promotion: { authorization: auth, promotion: { ...promotion, status: 'applied' } } });
  fireEvent.click(screen.getByText('刷新授权与交付状态'));
  await screen.findByText(/Apply 已完成/);
  expect(apply).toHaveBeenCalledTimes(1); expect(authorize).not.toHaveBeenCalled();
});


test('HTTP without randomUUID can authorize and retry with the same cryptographic key and body', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
  const source = globalThis.crypto;
  const random = jest.fn((bytes: Uint8Array) => source.getRandomValues(bytes));
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: random } });
  try {
    authorize.mockRejectedValueOnce(new Error('timeout'));
    render(<RsiManualPromotion scope={scope} report={report} />);
    confirmAuthorization(); fireEvent.click(screen.getByText('人工授权晋升（不 Apply）'));
    await screen.findByRole('alert');
    expect(authorize).toHaveBeenCalledTimes(1);
    expect(authorize.mock.calls[0][2]).toMatch(/^rsi-[0-9a-f]{32}$/);
    expect(random).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText('人工晋升理由（必填）') as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.click(screen.getByText('使用原请求重试授权'));
    await screen.findByText('Apply 到真实 Bot');
    expect(authorize.mock.calls[1]).toEqual(authorize.mock.calls[0]);
    expect(random).toHaveBeenCalledTimes(1);
    expect(apply).not.toHaveBeenCalled();
  } finally {
    Object.defineProperty(globalThis, 'crypto', descriptor);
  }
});


test.each(['missing', 'throwing'] as const)('unavailable random source (%s) reports unsent and keeps the form editable', async (mode) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: mode === 'missing' ? undefined : { getRandomValues: () => { throw new Error('private runtime detail'); } },
  });
  try {
    render(<RsiManualPromotion scope={scope} report={report} />);
    confirmAuthorization(); fireEvent.click(screen.getByText('人工授权晋升（不 Apply）'));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('本次操作尚未发送');
    expect(alert.textContent).not.toContain('private runtime detail');
    expect(authorize).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled();
    expect((screen.getByLabelText('人工晋升理由（必填）') as HTMLTextAreaElement).disabled).toBe(false);
    expect(screen.queryByText('使用原请求重试授权')).toBeNull();
  } finally {
    Object.defineProperty(globalThis, 'crypto', descriptor);
  }
});


test('authorization reason is bounded and editing it requires confirming the new request', () => {
  render(<RsiManualPromotion scope={scope} report={report} />);
  confirmAuthorization();
  const reason = screen.getByLabelText('人工晋升理由（必填）') as HTMLTextAreaElement;
  const submit = screen.getByText('人工授权晋升（不 Apply）') as HTMLButtonElement;
  expect(reason.maxLength).toBe(1000);
  expect(submit.disabled).toBe(false);
  fireEvent.change(reason, { target: { value: 'another reason' } });
  expect(submit.disabled).toBe(true);
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect(authorize).not.toHaveBeenCalled();
});

test('settled delivery is labelled as a historical receipt, not current live baseline verification', () => {
  render(<RsiManualPromotion scope={scope} report={{ ...authorized,
    manual_promotion: { authorization: auth, promotion: { ...promotion, status: 'applied' } },
  }} />);
  expect(screen.getByRole('status').textContent).toContain('此为历史回执');
  expect(screen.getByRole('status').textContent).toContain('当前基线与现场漂移请在基线管理中复查');
  expect(apply).not.toHaveBeenCalled();
});
