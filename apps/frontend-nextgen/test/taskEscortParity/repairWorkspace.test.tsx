/** @jest-environment jsdom */
import { RepairTaskWorkspace } from '@/pages/TaskEscort/parity/evolution/RepairTaskWorkspace';
import { repairService } from '@/services/taskEscortParity/repairService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
jest.mock('@/services/taskEscortParity/repairService', () => ({
  repairService: { task: jest.fn(), diff: jest.fn(), material: jest.fn(), trials: jest.fn(), verification: jest.fn() },
}));
const context = {
  scopeKey: 'u:wf',
  ready: true,
  userId: 'u',
  workflowId: 'wf',
  workflow: null,
  query: { repairTaskId: 't' },
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const item = {
  itemId: 'i',
  groupKey: 'g',
  proposalKey: 'p',
  contentRevision: 1,
  previousItemId: null,
  proposal: { summary: '真实优化目标' },
  instruction: 'frozen',
  sources: [{ kind: 'suggestion', suggestionId: 's' }],
};
const revision = {
  workflowId: 'wf',
  taskId: 't',
  revision: 2,
  phase: 'review',
  stateVersion: 1,
  input: { items: [item], baseline: { packCommit: 'base' }, materials: { recordId: 'mat' } },
  draft: { candidateCommit: 'candidate' },
  checks: { passed: true },
  error: null,
  createdAtMs: 1,
  updatedAtMs: 1,
};
beforeEach(() => {
  jest.clearAllMocks();
  (repairService.task as jest.Mock).mockResolvedValue({
    workflowId: 'wf',
    taskId: 't',
    latestAttempt: revision,
    latestSuccessful: revision,
    revisions: [revision],
    capabilities: { generation: true, diff: true, publication: true, reason: null },
    release: { approval: null, deployments: [] },
    revisionPage: { page: 1, pageSize: 20, total: 21, totalPages: 2 },
  });
  (repairService.trials as jest.Mock).mockResolvedValue({ configured: false, trials: [], nextOffset: null });
});
it('replaces workspace, supports exact tabs/history and reads real patch/checks; every write disabled', async () => {
  (repairService.diff as jest.Mock).mockResolvedValue({
    baseCommit: 'base',
    candidateCommit: 'candidate',
    files: [
      {
        path: 'flow.yaml',
        status: 'modified',
        before: 'a',
        after: 'b',
        binary: false,
        truncated: false,
        patch: '@@ -1 +1 @@\n-a\n+b',
      },
    ],
    truncated: false,
  });
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit />);
  expect(await screen.findByRole('tab', { name: '优化目标' })).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '改动与发布' }));
  expect(await screen.findByText('flow.yaml')).toBeInTheDocument();
  expect(await screen.findByText(/@@ -1/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '下一页版本历史' }));
  await waitFor(() => expect(repairService.task).toHaveBeenLastCalledWith('wf', 't', 'u', 2, expect.any(AbortSignal)));
});
it('reads revision frozen material only and preserves 404, no live original fallback', async () => {
  (repairService.material as jest.Mock).mockRejectedValue(new Error('404：冻结材料不存在'));
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit={false} />);
  fireEvent.click(await screen.findByRole('button', { name: '读取冻结材料 i' }));
  expect(await screen.findByText('404：冻结材料不存在')).toBeInTheDocument();
  expect(repairService.material).toHaveBeenCalledWith('t', 2, 'i', 'u', expect.any(AbortSignal));
});
it('does not read effect or claim success without a successful release receipt', async () => {
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit />);
  fireEvent.click(await screen.findByRole('tab', { name: '发布后效果' }));
  expect(await screen.findByText(/暂无成功发布回执/)).toBeInTheDocument();
  expect(repairService.verification).not.toHaveBeenCalled();
});
it('does not read diff when server capability is false', async () => {
  const d = await (repairService.task as jest.Mock)();
  (repairService.task as jest.Mock).mockResolvedValue({
    ...d,
    capabilities: { generation: false, diff: false, publication: false, reason: '未配置' },
  });
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit={false} />);
  fireEvent.click(await screen.findByRole('tab', { name: '改动与发布' }));
  expect(await screen.findByText(/差异能力不可用/)).toBeInTheDocument();
  expect(repairService.diff).not.toHaveBeenCalled();
});
