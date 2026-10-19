import { buildCreateRequest, initialDraft, validateDiagnosisSource } from '@/domain/evolution/createTask';
import type { Task } from '@/domain/evolution/types';
import { canManage, completedDiagnosis, isTaskType } from '@/domain/evolution/types';

const base = (type: Parameters<typeof initialDraft>[0]) => ({
  ...initialDraft(type, new Date(2026, 9, 14)),
  taskName: '演示任务',
  ownerId: 'owner',
  botId: 'bot',
  model: 'provider/model',
  botEnv: 'prod',
});

describe('ClawEvolve creation protocol', () => {
  it('preserves diagnosis + plan input and current defaults', () => {
    const { endpoint, data } = buildCreateRequest(base('diagnose'));
    expect(endpoint).toBe('diagnoses');
    expect(data).toMatchObject({
      userId: 'owner',
      botId: 'bot',
      botEnv: 'prod',
      model: 'provider/model',
      judgeBackend: 'subagent',
      sessionSource: 'local',
      startDate: '2026-10-07',
      endDate: '2026-10-14',
      maxSessions: 10,
      runtimeMaintenance: true,
    });
    expect(data.diagnoseIntent).toContain('抽取4个 bad case 和1个 good case');
    expect(data).not.toHaveProperty('apiKey');
    expect(data).not.toHaveProperty('stageSelection');
  });
  it('uses frozen Session selectors without a competing time range', () => {
    const request = buildCreateRequest({
      ...base('diagnose'),
      explicitSessions: true,
      sessionText: 'session-1\nsession-1\nsession-2',
    });
    expect(request.data).toMatchObject({
      sessionFilter: { mode: 'explicit', sessionIdentifiers: ['session-1', 'session-2'] },
      maxSessions: 2,
    });
    expect(request.data).not.toHaveProperty('startDate');
    expect(() => buildCreateRequest({ ...base('diagnose'), explicitSessions: true, sessionText: '' })).toThrow(
      'Session',
    );
  });
  it('creates only explicit direct-goal full tasks, preserving historical display independently', () => {
    expect(initialDraft('full').inputMode).toBe('direct_goal');
    for (const inputMode of ['direct_goal', 'diagnose_goal'] as const) {
      const direct = buildCreateRequest({ ...base('full'), inputMode, goal: '达到目标' });
      expect(direct).toMatchObject({
        endpoint: 'tasks',
        data: { taskType: 'full', inputMode: 'direct_goal', goal: '达到目标', maxRounds: 3 },
      });
      expect(direct.data).not.toHaveProperty('diagnoseIntent');
      expect(direct.data).not.toHaveProperty('sessionSource');
    }
    expect(() => buildCreateRequest(base('full'))).toThrow('进化目标');
    expect(() => buildCreateRequest({ ...base('full'), goal: 'x'.repeat(2001) })).toThrow('2000');
  });
  it('never replaces missing optimization inputs with a new diagnosis', () => {
    expect(() => buildCreateRequest(base('optimize'))).toThrow('诊断');
    expect(buildCreateRequest({ ...base('optimize'), sourceDiagnosisTaskId: 'source' })).toMatchObject({
      endpoint: 'optimizations',
      data: { sourceDiagnosisTaskIds: ['source'] },
    });
    expect(() => buildCreateRequest(base('bench_optimize'))).toThrow('训练/测试');
    expect(
      buildCreateRequest({
        ...base('bench_optimize'),
        trainDomainId: 'train',
        testDomainId: 'test',
        objective: '提升完成率',
      }),
    ).toMatchObject({
      endpoint: 'bench-optimizations',
      data: { trainBenchDomainId: 'train', testBenchDomainId: 'test', objective: '提升完成率' },
    });
  });
  it('keeps Bench evaluation distinct from optimization', () => {
    expect(buildCreateRequest({ ...base('bench'), benchDomainId: 'published' })).toMatchObject({
      endpoint: 'benches',
      data: { benchDomainId: 'published', suite: 'all', scene: 'claw-evolve-bench' },
    });
    expect(() => buildCreateRequest(base('bench'))).toThrow('Domain');
  });
  it('validates numeric limits and omits Skill evolution', () => {
    expect(() => buildCreateRequest({ ...base('diagnose'), badCases: 0, goodCases: 0 })).toThrow('总量');
    expect(() => buildCreateRequest({ ...base('full'), maxRounds: 0 })).toThrow('轮数');
    expect(isTaskType('hardening')).toBe(false);
    expect(isTaskType('skill')).toBe(false);
  });
  it('requires successful diagnosis and Plan; sharing does not grant manage access', () => {
    const task: Task = {
      task_id: 'task',
      task_name: 'diagnosis',
      bot_id: 'bot',
      status: 'completed',
      task_type: 'diagnose',
      created_by: 'creator',
      user_id: 'owner',
      config: { shared: true },
      steps: [
        { stepId: 'diagnose', stepType: 'diagnose', status: 'succeeded' },
        { stepId: 'plan', stepType: 'plan', status: 'failed' },
      ],
    };
    expect(completedDiagnosis(task)).toBe(false);
    task.steps![1].status = 'succeeded';
    expect(completedDiagnosis(task)).toBe(true);
    task.config.targetSkillAssetId = 'skill';
    expect(completedDiagnosis(task)).toBe(false);
    expect(canManage(task, 'owner', false)).toBe(false);
    expect(canManage(task, 'creator', false)).toBe(true);
  });
});

it('preserves retry/stop handling for failed initialization with a pending business step', () => {
  const { canRetryStep, canCancelStep } =
    require('@/domain/evolution/types') as typeof import('@/domain/evolution/types');
  const init = { stepId: 'init', stepType: 'skill_init', status: 'failed' };
  const task = { steps: [init, { stepId: 'plan', stepType: 'plan', status: 'created' }] } as Task;
  expect(canRetryStep(task, init)).toBe(true);
  init.status = 'running';
  expect(canCancelStep(task, init)).toBe(true);
  expect(canCancelStep(task, task.steps![1])).toBe(false);
  expect(canRetryStep(task, task.steps![1])).toBe(false);
});

it('validates range/explicit focus, byte limits and complete source detail before submission', () => {
  expect(() => buildCreateRequest({ ...base('diagnose'), focus: '' })).toThrow('关注问题');
  expect(() => buildCreateRequest({ ...base('diagnose'), focus: 'x'.repeat(1001) })).toThrow('1000');
  for (const length of [1, 20]) {
    const data = buildCreateRequest({
      ...base('diagnose'),
      explicitSessions: true,
      sessionText: Array.from({ length }, (_, i) => `s-${i}`).join('\n'),
      focus: '工具失败',
    }).data;
    expect(data.maxSessions).toBe(length);
    expect(data.diagnoseIntent).toContain('工具失败');
    expect(data).not.toHaveProperty('startDate');
  }
  expect(() =>
    buildCreateRequest({
      ...base('diagnose'),
      explicitSessions: true,
      sessionText: Array.from({ length: 21 }, (_, i) => `s-${i}`).join('\n'),
    }),
  ).toThrow('20');
  const bench = {
    ...base('bench_optimize'),
    trainDomainId: 'train',
    testDomainId: 'test',
    objective: '中'.repeat(1366),
  };
  expect(() => buildCreateRequest(bench)).toThrow('4KiB');
  expect(buildCreateRequest({ ...bench, objective: '中'.repeat(1365) }).data).toHaveProperty('objective');
  const source = { ...base('optimize'), sourceDiagnosisTaskId: 'source' };
  const task = {
    task_id: 'source',
    task_name: 'source',
    created_by: 'owner',
    status: 'completed',
    task_type: 'diagnose',
    user_id: 'owner',
    bot_id: 'bot',
    config: { botEnv: 'prod' },
    steps: [
      { stepId: 'd', stepType: 'diagnose', status: 'succeeded' },
      {
        stepId: 'p',
        stepType: 'plan',
        status: 'succeeded',
        output: { benchDomains: { trainBenchDomainId: 'train', testBenchDomainId: 'test' } },
      },
    ],
  } as Task;
  expect(() => validateDiagnosisSource(task, source)).not.toThrow();
  expect(() => validateDiagnosisSource({ ...task, user_id: 'other' }, source)).toThrow('同 Bot');
  expect(() =>
    validateDiagnosisSource({ ...task, steps: task.steps?.map((s) => ({ ...s, output: undefined })) }, source),
  ).toThrow('Plan');
});

it('keeps cross-author train/test namespaces separate from Bot execution Owner', () => {
  const request = buildCreateRequest({
    ...base('bench_optimize'),
    objective: 'Goal',
    trainDomainId: 'same',
    testDomainId: 'same',
    trainBenchOwnerUserId: 'alice',
    testBenchOwnerUserId: 'bob',
  });
  expect(request.data).toMatchObject({
    userId: 'owner',
    botId: 'bot',
    trainBenchOwnerUserId: 'alice',
    testBenchOwnerUserId: 'bob',
    trainBenchDomainId: 'same',
    testBenchDomainId: 'same',
  });
});
it('accepts a historical missing source environment only when Host proved the Bot environment unambiguous', () => {
  const draft = { ...base('optimize'), sourceDiagnosisTaskId: 'source' };
  const source = {
    task_id: 'source',
    task_name: 'Source',
    task_type: 'full',
    status: 'completed',
    created_by: 'member',
    user_id: 'owner',
    bot_id: 'bot',
    config: {},
    steps: [
      { stepId: 'd', stepType: 'diagnose', status: 'succeeded' },
      {
        stepId: 'p',
        stepType: 'plan',
        status: 'succeeded',
        output: { benchDomains: { trainBenchDomainId: 'train', testBenchDomainId: 'test' } },
      },
    ],
  } as Task;
  expect(() => validateDiagnosisSource(source, draft)).toThrow('同 Bot');
  expect(() => validateDiagnosisSource(source, { ...draft, unambiguousEnvironment: true })).not.toThrow();
});
