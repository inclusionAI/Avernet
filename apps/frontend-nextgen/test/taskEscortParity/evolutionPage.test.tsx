/** @jest-environment jsdom */
import { IssueEvidence } from '@/pages/TaskEscort/parity/evolution/IssueEvidence';
import { IssuePage } from '@/pages/TaskEscort/parity/evolution/IssuePage';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { repairService } from '@/services/taskEscortParity/repairService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
jest.mock('@/services/taskEscortParity/evolutionService', () => ({
  evolutionService: {
    access: jest.fn(),
    groups: jest.fn(),
    group: jest.fn(),
    diagnoses: jest.fn(),
    analysis: jest.fn(),
    suggestions: jest.fn(),
    applyTasks: jest.fn(),
  },
}));
jest.mock('@/services/taskEscortParity/repairService', () => ({
  repairService: { candidates: jest.fn(), item: jest.fn() },
}));
const context = {
  scopeKey: 'u:wf',
  ready: true,
  userId: 'u',
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const group = {
  workflowId: 'wf',
  signature: 'sig',
  inputDigest: 'd',
  flowIds: ['r'],
  sources: [
    {
      sourceId: 's',
      analysisId: 'a',
      diagnosisId: 'd',
      flowId: 'r',
      flowIds: ['r'],
      nodeId: 'n',
      failureMode: 'timeout',
      reasoning: '真实单次原因',
      completedAtMs: 1,
      evidenceEventIds: ['ev'],
      snapshot: null,
    },
  ],
  summary: null,
  summarySources: null,
  stale: true,
  aggregationStatus: 'not_generated',
  aggregationId: null,
  priority: { severity: 'high', day: { affected: 2 }, week: { affected: 4 }, lastOccurredAtMs: 1 },
};
beforeEach(() => {
  jest.clearAllMocks();
  (evolutionService.access as jest.Mock).mockResolvedValue({ workflowId: 'wf', canView: true, canEdit: true });
  (evolutionService.groups as jest.Mock).mockResolvedValue({
    groups: [group],
    facets: { nodes: ['n'], modes: ['timeout'] },
    page: { page: 1, pageSize: 10, total: 11, totalPages: 2 },
  });
  (evolutionService.group as jest.Mock).mockResolvedValue(group);
  (evolutionService.suggestions as jest.Mock).mockResolvedValue({ suggestions: [], total: 0, limit: 20, offset: 0 });
  (evolutionService.diagnoses as jest.Mock).mockResolvedValue({ diagnoses: [], total: 0, limit: 10, offset: 0 });
  (repairService.candidates as jest.Mock).mockResolvedValue({
    workflowId: 'wf',
    inputDigest: 'd',
    items: [],
    tasks: [],
    capabilities: { generation: true, diff: true, publication: true, reason: null },
    counts: {},
    page: { page: 1, pageSize: 20, total: 0, totalPages: 1 },
    facets: { nodes: [], failureModes: [] },
    canEdit: true,
    activeSnapshot: null,
    includeHistorical: false,
  });
});
it('renders exact three tabs and actual original causes, closes Escape and restores trigger focus', async () => {
  render(<IssuePage context={context} />);
  const trigger = await screen.findByRole('button', { name: '查看问题 sig' });
  trigger.focus();
  fireEvent.click(trigger);
  expect(await screen.findByRole('dialog', { name: '问题详情' })).toBeInTheDocument();
  for (const name of ['问题原因', '修复建议', '证据与历史'])
    expect(screen.getByRole('tab', { name })).toBeInTheDocument();
  expect(await screen.findByText('真实单次原因')).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});
it('requests server paging and include older without mutating', async () => {
  render(<IssuePage context={context} />);
  await screen.findByRole('button', { name: '查看问题 sig' });
  fireEvent.click(screen.getByRole('button', { name: '下一页问题' }));
  await waitFor(() =>
    expect(evolutionService.groups).toHaveBeenLastCalledWith(
      'wf',
      'u',
      expect.objectContaining({ page: 2, pageSize: 10, versionScope: 'current' }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole('checkbox', { name: '包含更早的建议' }));
  await waitFor(() =>
    expect(repairService.candidates).toHaveBeenCalledWith(
      'wf',
      'u',
      expect.objectContaining({ includeHistorical: true }),
      expect.any(AbortSignal),
    ),
  );
  expect(screen.getByRole('button', { name: '生成优化任务' })).toBeDisabled();
});
it('deep links resolve outside current page, default evidence and retain explicit analysis missing state', async () => {
  (evolutionService.analysis as jest.Mock).mockRejectedValue(new Error('404：所选分析不存在'));
  render(<IssuePage context={{ ...context, query: { issueSignature: 'sig', analysisId: 'a', runId: 'r' } }} />);
  expect(await screen.findByRole('tab', { name: '证据与历史' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByText(/404：所选分析不存在/)).toBeInTheDocument();
  expect(evolutionService.analysis).toHaveBeenCalledWith('wf', 'r', 'a', 'u', expect.any(AbortSignal));
});
it('keeps same-signature historical snapshots isolated in causes, evidence and repair candidate queries', async () => {
  const a = {
    status: 'resolved',
    snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 11, configDigest: 'digest-a' },
  };
  const b = {
    status: 'resolved',
    snapshot: { kind: 'deployment', workflowId: 'wf', workflowVersion: 2, deployNumber: 22, configDigest: 'digest-b' },
  };
  const keyA = JSON.stringify(['deployment', 'wf', 1, 11]),
    keyB = JSON.stringify(['deployment', 'wf', 2, 22]);
  const mixed = {
    ...group,
    flowIds: ['run-a', 'run-b'],
    sources: [
      {
        ...group.sources[0],
        sourceId: 'sa',
        flowId: 'run-a',
        flowIds: ['run-a'],
        reasoning: 'version A original reason',
        snapshot: a,
      },
      {
        ...group.sources[0],
        sourceId: 'sb',
        flowId: 'run-b',
        flowIds: ['run-b'],
        reasoning: 'version B original reason',
        snapshot: b,
      },
    ],
    summary: { summary: 'mixed-version summary must not show', causes: [], unknowns: [] },
  };
  (evolutionService.groups as jest.Mock).mockResolvedValue({
    groups: [mixed],
    facets: { nodes: [], modes: [] },
    page: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  });
  (evolutionService.group as jest.Mock).mockResolvedValue(mixed);
  render(<IssuePage context={context} />);
  const archive = (await screen.findByText('历史版本问题')).closest('details')!;
  archive.open = true;
  fireEvent(archive, new Event('toggle'));
  const versionA = (await screen.findByText(/工作流运行版本 1 · 部署 11 · 本页/)).closest('details')!;
  versionA.open = true;
  const versionB = screen.getByText(/工作流运行版本 2 · 部署 22 · 本页/).closest('details')!;
  versionB.open = true;
  const buttons = await screen.findAllByRole('button', { name: '查看历史问题 sig' });
  fireEvent.click(buttons[0]);
  const dialog = await screen.findByRole('dialog', { name: '问题详情' });
  await waitFor(() => expect(dialog).toHaveTextContent('version A original reason'));
  expect(dialog).not.toHaveTextContent('version B original reason');
  expect(dialog).not.toHaveTextContent('mixed-version summary must not show');
  fireEvent.click(screen.getByRole('tab', { name: '证据与历史' }));
  expect(await screen.findByRole('button', { name: '选择分析 run-a a' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '选择分析 run-b a' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '修复建议' }));
  await waitFor(() =>
    expect(repairService.candidates).toHaveBeenLastCalledWith(
      'wf',
      'u',
      expect.objectContaining({
        signature: 'sig',
        snapshotKey: keyA,
        versionScope: 'historical',
        includeHistorical: true,
      }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getAllByRole('button', { name: '查看历史问题 sig' })[1]);
  const second = await screen.findByRole('dialog');
  await waitFor(() => expect(second).toHaveTextContent('version B original reason'));
  expect(second).not.toHaveTextContent('version A original reason');
  fireEvent.click(screen.getByRole('tab', { name: '修复建议' }));
  await waitFor(() =>
    expect(repairService.candidates).toHaveBeenLastCalledWith(
      'wf',
      'u',
      expect.objectContaining({ snapshotKey: keyB }),
      expect.any(AbortSignal),
    ),
  );
});

it('does not read a query-selected analysis outside the archived snapshot sources', async () => {
  (evolutionService.analysis as jest.Mock).mockResolvedValue(null);
  render(
    <IssueEvidence
      context={{ ...context, query: { runId: 'outside-version-run', analysisId: 'outside-analysis' } }}
      group={group}
      snapshotKey={JSON.stringify(['deployment', 'wf', 1, 11])}
    />,
  );
  expect(await screen.findByText(/所选运行或分析不在该问题来源中/)).toBeInTheDocument();
  await waitFor(() => expect(evolutionService.analysis).not.toHaveBeenCalled());
});
