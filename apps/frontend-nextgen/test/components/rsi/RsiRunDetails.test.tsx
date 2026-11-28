/** @jest-environment jsdom */
import { RsiRunDetails } from '@/components/rsi/RsiRunDetails';
import * as api from '@/services/rsi/service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/rsi/service', () => ({
  ...jest.requireActual('@/services/rsi/service'),
  getRsiStrategyOutput: jest.fn(), getRsiCandidateReport: jest.fn(), getGenomeDiff: jest.fn(), cancelRsiRun: jest.fn(),
}));
const scope = { botId: 'bot-a', ownerId: 'owner', actorId: 'actor' };
const run: api.RsiRun = {
  id: 'run-a', binding_id: 'binding', strategy: 'behavior@1', status: 'completed', attempt: 1,
  parent_revision: 'sha256:parent', candidate_count: 1, created_at: '', end_reason: 'done',
  summary: { proposed: 1 }, budget_used: {},
};
const report: api.RsiCandidateReport = {
  candidate_id: 'candidate-a', revision_id: 'sha256:candidate', revision_seq: 2,
  parent_revision_id: 'sha256:parent', parent_revision_seq: 1, run_id: run.id, strategy: 'behavior', strategy_version: '1',
  patch_summary: [], diff_path: '', rationale: 'Improve clarity', evidence: [], verdict: 'inconclusive',
  splits: [], cost: {}, flags: [], gate: null, review: null,
};
const evidence: api.RsiEvidence = {
  jobs: [{ job_id: 'job-a', attempt: 1, worker_id: 'worker-a', lease_expires_at: null, open: 0 }],
  events: [{ id: 1, at: '', kind: 'candidate.persisted', authority: 'registry', sandbox_id: null, details: {} }],
  next_cursor: 1,
};
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(api.getRsiStrategyOutput).mockResolvedValue({ output: null });
  jest.mocked(api.getRsiCandidateReport).mockResolvedValue(report);
  jest.mocked(api.getGenomeDiff).mockResolvedValue({
    revision: report.revision_id, against: report.parent_revision_id, risk_tier: 'T2', rewrite_flags: [],
    changes: [{ gene: 'skills/editor/SKILL.md', kind: 'modified', diff: '-old\n+new' }],
  });
});
const act = async (action: () => Promise<void>) => action();
test('retains strategy summary, authority, jobs and candidate reports with exact immutable Diff', async () => {
  render(<RsiRunDetails scope={scope} selected={run} evidence={evidence}
    candidates={[{ candidate_id: report.candidate_id, revision: report.revision_id, verdict: report.verdict }]}
    act={act} busy={false} />);
  expect(screen.getByText(/"proposed": 1/)).toBeTruthy();
  expect(screen.getByText('registry')).toBeTruthy();
  expect(screen.getByText(/"job_id": "job-a"/)).toBeTruthy();
  expect(screen.queryByText('取消运行')).toBeNull();
  fireEvent.click(screen.getByText('候选报告'));
  await screen.findByText('modified · skills/editor/SKILL.md');
  expect(api.getGenomeDiff).toHaveBeenCalledWith(scope, report.revision_id, report.parent_revision_id);
  expect(screen.getByText(/不是模型建议/)).toBeTruthy();
});
test('unread candidates are not empty, and a running Run can still be cancelled', async () => {
  const view = render(<RsiRunDetails scope={scope} selected={{ ...run, status: 'running' }} evidence={null}
    candidates={null} act={act} busy={false} />);
  expect(screen.getByText('候选尚未读取成功。')).toBeTruthy();
  expect(screen.queryByText('该运行暂无候选。')).toBeNull();
  fireEvent.click(screen.getByText('取消运行'));
  await waitFor(() => expect(api.cancelRsiRun).toHaveBeenCalledWith(scope, run.id));
  view.rerender(<RsiRunDetails scope={scope} selected={run} evidence={evidence} candidates={[]} act={act} busy={false} />);
  expect(screen.getByText('该运行暂无候选。')).toBeTruthy();
});
