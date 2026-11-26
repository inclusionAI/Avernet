/** @jest-environment jsdom */
import { RsiCandidateCard } from '@/components/rsi/RsiReview';
import * as api from '@/services/rsi/service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/components/rsi/GenomeDiff', () => ({ RevisionDiff: () => null }));
jest.mock('@/services/rsi/service', () => ({
  ...jest.requireActual('@/services/rsi/service'),
  getRsiCandidateReport: jest.fn(), approveRsiCandidate: jest.fn(), rejectRsiCandidate: jest.fn(),
}));
const scope = { botId: 'bot', ownerId: 'owner', actorId: 'actor' };
const report: api.RsiCandidateReport = {
  candidate_id: 'candidate', revision_id: 'target', parent_revision_id: 'parent', run_id: 'run',
  revision_seq: 2, parent_revision_seq: 1, strategy: 'skill', strategy_version: '1', patch_summary: [],
  diff_path: '', rationale: '', evidence: [], verdict: 'inconclusive', splits: [], cost: {}, flags: [],
  gate: null, review: { status: 'open', created_at: '', decided_by: null, decided_at: null, decision_reason: null, promotion_id: null },
};

test.each(['approve', 'reject'] as const)('HTTP without randomUUID supports record-only risk review: %s', async (action) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!;
  const source = globalThis.crypto;
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true, value: { getRandomValues: (bytes: Uint8Array) => source.getRandomValues(bytes) },
  });
  try {
    jest.mocked(api.getRsiCandidateReport).mockResolvedValue(report);
    const decide = jest.mocked(action === 'approve' ? api.approveRsiCandidate : api.rejectRsiCandidate);
    decide.mockResolvedValue({ review: { ...report.review!, status: action === 'approve' ? 'approved' : 'rejected' } });
    render(<RsiCandidateCard scope={scope} candidate={{ candidate_id: 'candidate', revision: 'target', verdict: 'inconclusive' }}
      act={async (work) => { await work(); }} busy={false} />);
    fireEvent.click(screen.getByText('候选报告'));
    await screen.findByText('批准');
    fireEvent.change(screen.getByPlaceholderText('审批理由（必填，随结论一起审计）'), { target: { value: 'checked diff' } });
    fireEvent.click(screen.getByText(action === 'approve' ? '批准' : '拒绝'));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(scope, 'candidate', expect.stringMatching(/^rsi-[0-9a-f]{32}$/), 'checked diff'));
    await screen.findByText(action === 'approve' ? 'approved' : 'rejected');
  } finally {
    Object.defineProperty(globalThis, 'crypto', descriptor);
    jest.clearAllMocks();
  }
});
