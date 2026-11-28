/** @jest-environment jsdom */
import { GenomeBaselinePanel } from '@/components/rsi/GenomeBaselinePanel';
import * as api from '@/services/rsi/service';
import { genomeOperationTag, genomeScopeKey, useGenomePromotionStore } from '@/stores/genomePromotionStore';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/rsi/service', () => ({
  ...jest.requireActual('@/services/rsi/service'),
  captureGenomeRevision: jest.fn(), getGenomeDiff: jest.fn(), getGenomeDrift: jest.fn(), getGenomePromotion: jest.fn(),
  getGenomeRefs: jest.fn(), listGenomePromotions: jest.fn(), promoteGenomeRevision: jest.fn(),
}));
const refs = jest.mocked(api.getGenomeRefs);
const drift = jest.mocked(api.getGenomeDrift);
const capture = jest.mocked(api.captureGenomeRevision);
const diff = jest.mocked(api.getGenomeDiff);
const promote = jest.mocked(api.promoteGenomeRevision);
const history = jest.mocked(api.listGenomePromotions);
const getPromotion = jest.mocked(api.getGenomePromotion);
const scope = { botId: 'bot-a', ownerId: 'owner', actorId: 'actor' };
const promotion: api.GenomePromotion = {
  id: 'promotion-a', revision: 'sha256:live', from_revision: 'sha256:base', status: 'applying',
  apply_id: 'apply-a', apply_detail: '', reason: 'explicit owner reconciliation', created_at: '',
};
const diffValue: api.GenomeDiff = {
  revision: 'sha256:live', against: 'sha256:base', risk_tier: 'T2', rewrite_flags: [],
  changes: [{ gene: 'skills/editor/SKILL.md', kind: 'modified', diff: '-old\n+new' }],
};
beforeEach(() => {
  jest.clearAllMocks();
  sessionStorage.clear();
  useGenomePromotionStore.setState({ attempts: {} });
  refs.mockResolvedValue({ refs: [{ name: 'active', revision: 'sha256:base', labels: [] }] });
  drift.mockResolvedValue({ drift: { active: 'sha256:base', observed: 'sha256:live' } });
  history.mockResolvedValue({ items: [], total: 0 });
  capture.mockResolvedValue({ id: 'sha256:live', status: 'draft', created: true });
  diff.mockResolvedValue(diffValue);
  promote.mockImplementation(async (_scope, _revision, _expected, reason) => ({ ...promotion, reason }));
  getPromotion.mockImplementation(async () => {
    const pending = useGenomePromotionStore.getState().attempts[genomeScopeKey(scope)];
    return { ...promotion, reason: `${promotion.reason}\n${genomeOperationTag(pending)}` };
  });
});
async function openPanel() {
  const view = render(<GenomeBaselinePanel scope={scope} />);
  fireEvent.click(screen.getByText('查看基线与现场差异'));
  await screen.findByText(/检测到带外变更/);
  return view;
}
async function preview() {
  fireEvent.click(screen.getByText('捕获现场并预览全量差异'));
  await screen.findByText('modified · skills/editor/SKILL.md');
}
function confirm() {
  fireEvent.change(screen.getByLabelText('调和理由（必填）'), { target: { value: 'explicit owner reconciliation' } });
  fireEvent.click(screen.getByRole('checkbox'));
}

test('opening and refreshing are read-only; capture and exact full diff still do not apply', async () => {
  const view = render(<GenomeBaselinePanel scope={scope} />);
  expect(refs).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('查看基线与现场差异'));
  await screen.findByText(/检测到带外变更/);
  expect(capture).not.toHaveBeenCalled();
  await preview();
  expect(diff).toHaveBeenCalledWith(scope, 'sha256:live', 'sha256:base');
  expect(promote).not.toHaveBeenCalled();
  expect((screen.getByText('确认调和并启动 Apply') as HTMLButtonElement).disabled).toBe(true);
  expect(view.container.textContent).toContain('整个 Genome 快照');
});

test('requires reason and confirmation, passes CAS, then waits for settlement and re-reads actual state', async () => {
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await waitFor(() => expect(promote).toHaveBeenCalledTimes(1));
  expect(promote).toHaveBeenCalledWith(scope, 'sha256:live', 'sha256:base', expect.stringMatching(/^explicit owner reconciliation\n\[client-operation:[0-9a-f]{32}\]$/));
  await screen.findByText('晋升 promotion-a · applying');
  expect(screen.queryByText(/可由你提交新的/)).toBeNull();
  getPromotion.mockResolvedValue({ ...promotion, reason: promote.mock.calls[0][3], status: 'applied' });
  refs.mockResolvedValue({ refs: [{ name: 'active', revision: promotion.revision, labels: [] }] });
  drift.mockResolvedValue({ drift: null });
  fireEvent.click(screen.getByText('重新读取基线状态'));
  await screen.findByText(/Apply 已完成，active 已指向目标/);
  expect(useGenomePromotionStore.getState().attempts).toEqual({});
  expect(promote).toHaveBeenCalledTimes(1);
});

test.each(['candidate', 'rejected', 'archived'] as const)('capture preserves %s status; no laundering into owner draft', async (status) => {
  capture.mockResolvedValue({ id: 'sha256:live', status, created: false });
  await openPanel(); await preview();
  expect(screen.getByText(/不能通过重新捕获绕过/)).toBeTruthy();
  expect(screen.queryByText('确认调和并启动 Apply')).toBeNull();
  expect(promote).not.toHaveBeenCalled();
});

test.each(['live', 'active'])('refuses stale %s before submission, never auto-recaptures or promotes', async (changed) => {
  await openPanel(); await preview(); confirm();
  if (changed === 'live') drift.mockResolvedValue({ drift: { active: 'sha256:base', observed: 'sha256:new' } });
  else {
    refs.mockResolvedValue({ refs: [{ name: 'active', revision: 'sha256:new', labels: [] }] });
    drift.mockResolvedValue({ drift: { active: 'sha256:new', observed: 'sha256:live' } });
  }
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText(/本次未提交晋升/);
  expect(promote).not.toHaveBeenCalled();
  expect(capture).toHaveBeenCalledTimes(1);
});

test('unknown POST is not replayed, survives closing, can only recover by explicitly tracking a matching receipt', async () => {
  promote.mockRejectedValue(new Error('timeout'));
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText(/提交结果未知/);
  expect(sessionStorage.getItem('avernet-genome-promotions-v1')).toContain('sha256:live');
  fireEvent.click(screen.getByText('收起基线管理'));
  const matching = { ...promotion, reason: promote.mock.calls[0][3] };
  history.mockResolvedValue({ items: [matching, { ...promotion, id: 'older-same-target' }], total: 2 });
  fireEvent.click(screen.getByText('查看基线与现场差异'));
  await screen.findByText('跟踪这条晋升');
  expect(screen.getAllByText('跟踪这条晋升')).toHaveLength(1);
  expect((screen.getByText('捕获现场并预览全量差异') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('跟踪这条晋升'));
  await waitFor(() => expect(getPromotion).toHaveBeenCalledWith(scope, 'promotion-a'));
  expect(promote).toHaveBeenCalledTimes(1);
});

test.each(['applied_partial', 'apply_failed'] as const)('does not present %s as acceptance success', async (status) => {
  getPromotion.mockImplementation(async () => ({ ...promotion, reason: promote.mock.calls[0][3], status }));
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText(`晋升 promotion-a · ${status}`);
  expect(screen.queryByText(/可由你提交新的/)).toBeNull();
});

test('409 is explicit and correctable without an automatic POST retry', async () => {
  promote.mockRejectedValue(Object.assign(new Error('conflict'), { status: 409 }));
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText(/晋升冲突（409）/);
  expect(useGenomePromotionStore.getState().attempts).toEqual({});
  expect(promote).toHaveBeenCalledTimes(1);
});

test('read failure is not no-drift or safe-to-apply; missing active is distinguished from null drift', async () => {
  drift.mockRejectedValueOnce(new Error('read failed'));
  render(<GenomeBaselinePanel scope={scope} />);
  fireEvent.click(screen.getByText('查看基线与现场差异'));
  await screen.findByRole('alert');
  expect(screen.queryByText('本次观察未检测到漂移。')).toBeNull();
  refs.mockResolvedValue({ refs: [] }); drift.mockResolvedValue({ drift: null });
  fireEvent.click(screen.getByText('重新读取基线状态'));
  await screen.findByText(/未建立（drift 为空不代表已有基线）/);
  expect((screen.getByText('捕获现场并预览全量差异') as HTMLButtonElement).disabled).toBe(true);
  expect(capture).not.toHaveBeenCalled();
});

test('switching Bot while capture is pending discards old previews and cannot submit an old Bot Apply', async () => {
  let resolveOld!: (value: api.GenomeCapture) => void;
  capture.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  const view = await openPanel();
  fireEvent.click(screen.getByText('捕获现场并预览全量差异'));
  await waitFor(() => expect(capture).toHaveBeenCalledTimes(1));
  view.rerender(<GenomeBaselinePanel scope={{ ...scope, botId: 'bot-b' }} />);
  await screen.findByText(/检测到带外变更/);
  await act(async () => resolveOld({ id: 'sha256:live', status: 'draft', created: true }));
  expect(screen.queryByText('modified · skills/editor/SKILL.md')).toBeNull();
  expect(diff).not.toHaveBeenCalled(); expect(promote).not.toHaveBeenCalled();
});

test('an in-flight promotion blocks capture without an automatic write', async () => {
  history.mockResolvedValue({ items: [promotion], total: 1 });
  await openPanel();
  expect((screen.getByText('捕获现场并预览全量差异') as HTMLButtonElement).disabled).toBe(true);
  expect(capture).not.toHaveBeenCalled(); expect(promote).not.toHaveBeenCalled();
});

test.each(['drift', 'partial'] as const)('applied with remaining %s is not reconciled', async (kind) => {
  getPromotion.mockImplementation(async () => {
    refs.mockResolvedValue({ refs: [{ name: 'active', revision: promotion.revision, labels: kind === 'partial' ? ['partial'] : [] }] });
    drift.mockResolvedValue({ drift: kind === 'drift' ? { active: promotion.revision, observed: 'sha256:new' } : null });
    return { ...promotion, reason: promote.mock.calls[0][3], status: 'applied' };
  });
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await waitFor(() => expect(useGenomePromotionStore.getState().attempts).toEqual({}));
  await screen.findByText(/交付记录为 applied，但当前/);
  expect(screen.queryByText(/可由你提交新的/)).toBeNull();
});

test('switching Bot during preflight prevents the old-scope POST', async () => {
  const view = await openPanel(); await preview(); confirm();
  let resolveRefs!: (value: Awaited<ReturnType<typeof api.getGenomeRefs>>) => void;
  refs.mockImplementationOnce(() => new Promise((resolve) => { resolveRefs = resolve; }));
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText('基线状态尚未读取成功。');
  view.rerender(<GenomeBaselinePanel scope={{ ...scope, botId: 'bot-b' }} />);
  await screen.findByText(/检测到带外变更/);
  await act(async () => resolveRefs({ refs: [{ name: 'active', revision: 'sha256:base', labels: [] }] }));
  expect(promote).not.toHaveBeenCalled();
  expect(useGenomePromotionStore.getState().attempts).toEqual({});
});

test('mismatched promotion receipt stays uncertain and never polls the unrelated promotion', async () => {
  promote.mockImplementation(async (_scope, _revision, _expected, reason) => ({ ...promotion, reason, revision: 'sha256:other' }));
  await openPanel(); await preview(); confirm();
  fireEvent.click(screen.getByText('确认调和并启动 Apply'));
  await screen.findByText(/提交结果未知/);
  expect(getPromotion).not.toHaveBeenCalled();
  expect(promote).toHaveBeenCalledTimes(1);
});
