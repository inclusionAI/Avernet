/** @jest-environment jsdom */
// Load Jest before the Umi-dependent page graph (same convention as shell tests).
import { beforeEach, expect, it, jest } from '@jest/globals';

import EvolutionFactory from '@/pages/EvolutionFactory';
import CreateTask from '@/pages/EvolutionFactory/CreateTask';
import DomainDetail from '@/pages/EvolutionFactory/DomainDetail';
import { StepOutput } from '@/pages/EvolutionFactory/StepOutput';
import { evolutionApi } from '@/services/evolution/evolutionService';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history, useLocation } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: jest.fn(() => ({ search: '' })),
  useParams: () => ({ ownerUserId: 'owner', domainId: 'domain' }),
}));
jest.mock('@/pages/EvolutionFactory/useEvolutionContext', () => ({
  useEvolutionContext: () => ({
    api: jest.requireMock<typeof import('@/services/evolution/evolutionService')>(
      '@/services/evolution/evolutionService',
    ).evolutionApi,
    ready: true,
    userId: 'owner',
    key: 'owner:42',
    spaceId: 42,
    loading: false,
  }),
}));
jest.mock('@/services/backendApi/bots/botController', () => ({ listBots: jest.fn() }));
jest.mock('@/services/evolution/evolutionService', () => ({
  evolutionApi: {
    models: jest.fn(),
    bots: jest.fn(),
    versions: jest.fn(),
    packs: jest.fn(),
    tasks: jest.fn(),
    create: jest.fn(),
    domains: jest.fn(),
    domain: jest.fn(),
    templates: jest.fn(),
    runs: jest.fn(),
    publish: jest.fn(),
    task: jest.fn(),
    stepAction: jest.fn(),
  },
}));
// jsdom cannot emulate Radix pointer capture; keep the real page/fields and adapt only Choice.
jest.mock('@/pages/EvolutionFactory/shared', () => ({
  ...jest.requireActual<typeof import('@/pages/EvolutionFactory/shared')>('@/pages/EvolutionFactory/shared'),
  Choice: ({
    label,
    value,
    onChange,
    options,
    disabled,
  }: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    options: { value: string; label: string }[];
    disabled?: boolean;
  }) => (
    <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">请选择</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));
const api = jest.mocked(evolutionApi);
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(useLocation).mockReturnValue({ search: '' } as ReturnType<typeof useLocation>);
  api.models.mockResolvedValue({ defaultModel: 'provider/default', models: ['provider/default', 'provider/other'] });
  api.bots.mockResolvedValue({
    items: [{ ownerId: 'owner', botId: 'bot', botName: '测试 Bot', env: 'pre', activeEngine: 'openclaw' }],
    total: 1,
    page: 1,
    pageSize: 50,
  });
  api.tasks.mockResolvedValue({ tasks: [], total: 0, page: 1, pageSize: 20, totalPages: 1, canViewAll: false });
  api.domains.mockResolvedValue([]);
});
it('offers exactly five Bot types and keeps the template Tab in the URL', async () => {
  render(<EvolutionFactory />);
  expect(screen.getByRole('tablist', { name: '智能进化' })).toBeInTheDocument();
  for (const name of ['进化任务', '评测模板', '进化版本'])
    expect(screen.getByRole('tab', { name })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '发起进化' }));
  for (const name of ['Bot 诊断任务', 'Bench 诊断任务', 'Bench 优化任务', '诊断后优化任务', 'Bot 自进化'])
    expect(screen.getByRole('button', { name: new RegExp(name) })).toBeInTheDocument();
  expect(screen.queryByText('Skill 进化')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Bench 优化任务/ }));
  expect(history.push).toHaveBeenCalledWith('/evolution-factory/new?type=bench_optimize');
});
it('creates direct-goal tasks with selected model and Bot Owner, not actor fallback or diagnosis', async () => {
  jest.mocked(useLocation).mockReturnValue({ search: '?type=full' } as ReturnType<typeof useLocation>);
  api.create.mockResolvedValue({ task_id: 'task-1' } as Awaited<ReturnType<typeof evolutionApi.create>>);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('模型')).toHaveValue('provider/default'));
  fireEvent.change(screen.getByLabelText('任务名称 *'), { target: { value: '目标测试' } });
  fireEvent.click(screen.getByRole('radio', { name: /测试 Bot/ }));
  expect(screen.queryByLabelText('进化方式')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Bot 运行环境')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('目标 *'), { target: { value: '固定问候' } });
  fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'provider/other' } });
  fireEvent.submit(screen.getByRole('button', { name: '创建任务' }).closest('form')!);
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(
      'tasks',
      expect.objectContaining({
        taskType: 'full',
        inputMode: 'direct_goal',
        goal: '固定问候',
        userId: 'owner',
        botId: 'bot',
        model: 'provider/other',
      }),
    ),
  );
  expect(api.create.mock.calls[0][1]).not.toHaveProperty('diagnoseIntent');
  expect(history.replace).toHaveBeenCalledWith('/evolution-factory/tasks/task-1?created=1', {
    createdTask: { task_id: 'task-1' },
    spaceKey: 'owner:42',
  });
});
it('does not submit an optimization without a completed diagnosis/Plan source', async () => {
  jest.mocked(useLocation).mockReturnValue({ search: '?type=optimize' } as ReturnType<typeof useLocation>);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('模型')).toHaveValue('provider/default'));
  fireEvent.change(screen.getByLabelText('任务名称 *'), { target: { value: '优化' } });
  fireEvent.click(screen.getByRole('radio', { name: /测试 Bot/ }));
  fireEvent.submit(screen.getByRole('button', { name: '创建任务' }).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('已完成诊断和 Plan');
  expect(api.create).not.toHaveBeenCalled();
});
it('displays per-template publication failures rather than treating HTTP 200 as total success', async () => {
  api.domain.mockResolvedValue({
    domainId: 'domain',
    ownerUserId: 'owner',
    name: 'Demo Domain',
    status: 'active',
    templateCount: 1,
    description: '',
  });
  api.templates.mockResolvedValue([
    {
      templateName: 'case',
      displayName: 'Case',
      description: '',
      status: 'draft',
      latestVersion: 2,
      publishedVersion: null,
      versions: [{ version: 2, status: 'draft' }],
    },
  ]);
  api.runs.mockResolvedValue({ runs: [], total: 0 });
  api.publish.mockResolvedValue({ items: [{ templateName: 'case', success: false, reason: 'invalid template' }] });
  render(<DomainDetail />);
  expect(screen.queryByRole('tablist', { name: '智能进化' })).not.toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '目标详情' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '关联任务' })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole('checkbox', { name: '选择模板 case' }));
  fireEvent.click(screen.getByRole('button', { name: '批量发布' }));
  await waitFor(() =>
    expect(api.publish).toHaveBeenCalledWith('owner', 'domain', [{ templateName: 'case', version: 2 }]),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid template');
});
it('renders real diagnosis/Spec and routes only registered Bench run identifiers', () => {
  render(
    <StepOutput
      output={{
        diagnosis: { summary: '工具调用失败', issues: [] },
        spec: { version: 'v0', content: '# Goal\n固定问候' },
        baseline: { train: { benchRunId: 'run/1' } },
      }}
    />,
  );
  expect(screen.getByText('工具调用失败')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Goal' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '评估结果 · run/1' }));
  expect(history.push).toHaveBeenCalledWith('/evolution-factory/bench/runs/run%2F1');
});

it.each([
  ['diagnose', ['任务信息', '进化对象', '诊断范围', '诊断设置']],
  ['bench', ['任务信息', '进化对象', '评测配置']],
  ['bench_optimize', ['任务信息', '进化对象', '优化配置']],
  ['optimize', ['任务信息', '进化对象', '诊断配置']],
  ['full', ['任务信息', '进化对象', '进化配置']],
])('uses PRD sections and only configured models for %s', async (type, titles) => {
  jest.mocked(useLocation).mockReturnValue({ search: `?type=${type}` } as ReturnType<typeof useLocation>);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('模型')).toHaveValue('provider/default'));
  for (const title of titles) expect(screen.getByRole('heading', { name: new RegExp(title) })).toBeInTheDocument();
  expect(screen.getByLabelText('模型').tagName).toBe('SELECT');
  expect(screen.queryByLabelText('Bot 运行环境')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('进化方式')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByText('API Key')).not.toBeInTheDocument();
});
it('retains focus in exact Session mode and hides ineffective range fields', async () => {
  jest.mocked(useLocation).mockReturnValue({ search: '?type=diagnose' } as ReturnType<typeof useLocation>);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('模型')).toHaveValue('provider/default'));
  expect(screen.getByLabelText('会话时间范围')).toHaveValue('7');
  fireEvent.click(screen.getByRole('radio', { name: '按精确 Session' }));
  expect(screen.queryByLabelText('会话时间范围')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Bad Case 数量')).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Session 标识/)).toHaveAttribute('rows', '6');
  expect(screen.getByLabelText('关注问题 *')).not.toHaveValue('');
});
it('does not default to the first model when configuration has no default', async () => {
  api.models.mockResolvedValue({ defaultModel: '', models: ['provider/other'] });
  jest.mocked(useLocation).mockReturnValue({ search: '?type=full' } as ReturnType<typeof useLocation>);
  render(<CreateTask />);
  expect(await screen.findByRole('alert')).toHaveTextContent('未配置默认模型');
  expect(screen.getByRole('button', { name: '创建任务' })).toBeDisabled();
  expect(api.create).not.toHaveBeenCalled();
});
it('loads selected diagnosis detail for complete Plan validation, without inventing a new source', async () => {
  jest.mocked(useLocation).mockReturnValue({ search: '?type=optimize' } as ReturnType<typeof useLocation>);
  const source = {
    task_id: 'source',
    task_name: '诊断来源',
    task_type: 'diagnose',
    status: 'completed',
    user_id: 'owner',
    bot_id: 'bot',
    created_by: 'owner',
    config: { botEnv: 'pre' },
    steps: [
      { stepId: 'd', stepType: 'diagnose', status: 'succeeded' },
      { stepId: 'p', stepType: 'plan', status: 'succeeded' },
    ],
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  api.tasks.mockResolvedValue({ tasks: [source], total: 1, page: 1, pageSize: 50, totalPages: 1, canViewAll: false });
  api.task.mockResolvedValue(source);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('模型')).toHaveValue('provider/default'));
  fireEvent.change(screen.getByLabelText('任务名称 *'), { target: { value: '优化' } });
  fireEvent.click(screen.getByRole('radio', { name: /测试 Bot/ }));
  fireEvent.change(screen.getByLabelText('来源诊断'), { target: { value: 'source' } });
  fireEvent.submit(screen.getByRole('button', { name: '创建任务' }).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('Plan 缺少');
  expect(api.task).toHaveBeenCalledWith('source');
  expect(api.create).not.toHaveBeenCalled();
  api.task.mockResolvedValue({
    ...source,
    steps: source.steps?.map((step) =>
      step.stepType === 'plan'
        ? { ...step, output: { benchDomains: { trainBenchDomainId: 'train', testBenchDomainId: 'test' } } }
        : step,
    ),
  });
  api.create.mockResolvedValue({ ...source, task_id: 'new-task', task_type: 'optimize' });
  fireEvent.submit(screen.getByRole('button', { name: '创建任务' }).closest('form')!);
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(
      'optimizations',
      expect.objectContaining({ sourceDiagnosisTaskIds: ['source'] }),
    ),
  );
});

it('uses the same Step retry qualification for all current-space members', async () => {
  const failed = {
    task_id: 'failed-task',
    task_name: '失败任务',
    task_type: 'full',
    status: 'failed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
    steps: [{ stepId: 'failed-step', stepType: 'plan', status: 'failed' }],
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  api.tasks.mockResolvedValue({ tasks: [failed], total: 1, page: 1, pageSize: 20, totalPages: 1, canViewAll: false });
  api.stepAction.mockResolvedValue({});
  render(<EvolutionFactory />);
  fireEvent.click(await screen.findByRole('button', { name: '重试' }));
  await waitFor(() => expect(api.stepAction).toHaveBeenCalledWith('failed-task', 'failed-step', 'retry'));
  api.tasks.mockResolvedValue({
    tasks: [{ ...failed, created_by: 'other' }],
    total: 1,
    page: 1,
    pageSize: 20,
    totalPages: 1,
    canViewAll: false,
  });
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  await screen.findByText('other');
  expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
});

it('can retry failed preparation with a pending business step using the existing initialization rule', async () => {
  const task = {
    task_id: 'init-task',
    task_name: '准备失败',
    task_type: 'full',
    status: 'failed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
    steps: [
      { stepId: 'init-step', stepType: 'skill_init', status: 'failed' },
      { stepId: 'pending-step', stepType: 'plan', status: 'created' },
    ],
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  api.tasks.mockResolvedValue({ tasks: [task], total: 1, page: 1, pageSize: 20, totalPages: 1, canViewAll: false });
  api.stepAction.mockResolvedValue({});
  render(<EvolutionFactory />);
  fireEvent.click(await screen.findByRole('button', { name: '重试' }));
  await waitFor(() => expect(api.stepAction).toHaveBeenCalledWith('init-task', 'init-step', 'retry'));
  expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
});

it('renders start/end from raw API Step date strings without a creation-time fallback', async () => {
  const startedAt = '2026-10-14T10:20:30.000Z';
  const completedAt = '2026-10-14T11:20:30.000Z';
  const task = {
    task_id: 'dated-task',
    task_name: '时间字段验证',
    task_type: 'full',
    status: 'completed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
    gmt_create: '2026-10-14T09:00:00.000Z',
    steps: [{ stepId: 'plan', stepType: 'plan', status: 'succeeded', startedAt, completedAt }],
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  api.tasks.mockResolvedValue({ tasks: [task], total: 1, page: 1, pageSize: 20, totalPages: 1, canViewAll: false });
  render(<EvolutionFactory />);
  await screen.findByText('时间字段验证');
  const cell = screen.getByText(
    (_, el) =>
      el?.tagName === 'TD' &&
      el.textContent === new Date(startedAt).toLocaleString() + new Date(completedAt).toLocaleString(),
  );
  expect(cell).toBeInTheDocument();
  expect(screen.queryByText(new Date(task.gmt_create!).toLocaleString())).not.toBeInTheDocument();
});

it('shows only the five scoped Bot types and keeps honest server pagination', async () => {
  const base = {
    task_id: 'base',
    task_name: 'base',
    task_type: 'diagnose',
    status: 'completed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
    steps: [],
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  const allowed = ['diagnose', 'bench', 'bench_optimize', 'optimize', 'full'];
  const excluded = [
    'session_analysis',
    'session_export',
    'repair',
    'workflow_repair',
    'hardening',
    'apply',
    'pack',
    'pack_restore',
    'runtime_cleanup',
  ];
  const tasks = [
    ...allowed.map((type) => ({ ...base, task_id: type, task_name: `允许-${type}`, task_type: type })),
    ...excluded.map((type) => ({ ...base, task_id: type, task_name: `排除-${type}`, task_type: type })),
    { ...base, task_id: 'skill', task_name: '排除-Skill进化', config: { targetSkillAssetId: 'skill-1' } },
  ];
  api.tasks.mockResolvedValue({ tasks, total: 45, page: 1, pageSize: 20, totalPages: 3, canViewAll: false });
  render(<EvolutionFactory />);
  await screen.findByText('允许-diagnose');
  for (const type of allowed) expect(screen.getByText(`允许-${type}`)).toBeInTheDocument();
  for (const type of excluded) expect(screen.queryByText(`排除-${type}`)).not.toBeInTheDocument();
  expect(screen.queryByText('排除-Skill进化')).not.toBeInTheDocument();
  expect(screen.getByText(/任务列表返回了筛选范围外的数据/)).toBeInTheDocument();
  expect(screen.queryByText('共 45 条 · 第 1/3 页')).not.toBeInTheDocument();
  expect(api.tasks).toHaveBeenCalledWith(
    expect.objectContaining({ taskTypes: 'diagnose,bench,bench_optimize,optimize,full', targetType: 'bot' }),
  );
});
it('does not call an excluded-only page an empty task collection', async () => {
  const excluded = {
    task_id: 'session-task',
    task_name: '不展示的会话诊断',
    task_type: 'session_analysis',
    status: 'completed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
  } as Awaited<ReturnType<typeof evolutionApi.task>>;
  api.tasks.mockResolvedValue({
    tasks: [excluded],
    total: 40,
    page: 1,
    pageSize: 20,
    totalPages: 2,
    canViewAll: false,
  });
  render(<EvolutionFactory />);
  expect(await screen.findByText(/任务列表返回了筛选范围外的数据/)).toBeInTheDocument();
  expect(screen.queryByText('不展示的会话诊断')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument();
});

it('uses the filtered server total for ordinary list pagination', async () => {
  const task = {
    task_id: 'bot-diagnosis',
    task_name: '范围内诊断',
    task_type: 'diagnose',
    status: 'completed',
    user_id: 'owner',
    created_by: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'pre' },
    steps: [],
  };
  api.tasks.mockResolvedValue({ tasks: [task], total: 21, page: 1, pageSize: 20, totalPages: 2, canViewAll: false });
  render(<EvolutionFactory />);
  await screen.findByText('范围内诊断');
  expect(screen.getByText('共 21 条 · 第 1/2 页')).toBeInTheDocument();
  expect(screen.queryByText(/任务列表返回了筛选范围外的数据/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() =>
    expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, targetType: 'bot' })),
  );
});

it('keeps Pack creation an auxiliary version operation rather than a diagnosis', async () => {
  jest.mocked(useLocation).mockReturnValue({ search: '?type=pack' } as ReturnType<typeof useLocation>);
  api.create.mockResolvedValue({ task_id: 'pack-task' } as Awaited<ReturnType<typeof evolutionApi.create>>);
  render(<CreateTask />);
  await waitFor(() => expect(screen.getByLabelText('目标 Bot')).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText('任务名称'), { target: { value: 'Snapshot' } });
  fireEvent.change(screen.getByLabelText('目标 Bot'), { target: { value: JSON.stringify(['owner', 'bot', 'pre']) } });
  fireEvent.submit(screen.getByRole('button', { name: '确认创建' }).closest('form')!);
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(
      'packs',
      expect.objectContaining({ userId: 'owner', botId: 'bot', botEnv: 'pre' }),
    ),
  );
  expect(api.create.mock.calls[0][1]).not.toHaveProperty('diagnoseIntent');
});
