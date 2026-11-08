import type { JsonObject, Task, TaskType } from './types';
export interface CreateDraft {
  type: TaskType;
  taskName: string;
  remark: string;
  ownerId: string;
  botId: string;
  botEnv: string;
  unambiguousEnvironment?: boolean;
  model: string;
  maxRounds: number;
  inputMode: 'diagnose_goal' | 'direct_goal';
  goal: string;
  explicitSessions: boolean;
  sessionText: string;
  startDate: string;
  endDate: string;
  maxSessions: number;
  badCases: number;
  goodCases: number;
  focus: string;
  sourceDiagnosisTaskId: string;
  benchDomainOwnerUserId?: string;
  trainBenchOwnerUserId?: string;
  testBenchOwnerUserId?: string;
  benchDomainId: string;
  trainDomainId: string;
  testDomainId: string;
  objective: string;
  runtimeMaintenance: boolean;
}
export function localDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(
    2,
    '0',
  )}`;
}
export function initialDraft(type: TaskType, now = new Date()): CreateDraft {
  const start = new Date(now);
  start.setDate(start.getDate() - 7);
  return {
    type,
    taskName: '',
    remark: '',
    ownerId: '',
    botId: '',
    botEnv: '',
    model: '',
    maxRounds: 3,
    inputMode: 'direct_goal',
    goal: '',
    explicitSessions: false,
    sessionText: '',
    startDate: localDate(start),
    endDate: localDate(now),
    maxSessions: 10,
    badCases: 4,
    goodCases: 1,
    focus: '影响任务完成率的主要问题，优先关注工具调用失败、任务未完成和未经验证的回答',
    sourceDiagnosisTaskId: '',
    benchDomainId: '',
    trainDomainId: '',
    testDomainId: '',
    objective: '',
    runtimeMaintenance: true,
  };
}
function integer(value: number, min: number, max: number, label: string) {
  if (!Number.isSafeInteger(value) || value < min || value > max)
    throw new Error(`${label}需为 ${min} 到 ${max} 的整数`);
}
export function buildCreateRequest(d: CreateDraft): { endpoint: string; data: JsonObject } {
  if (!d.taskName.trim() || !d.botId || !d.ownerId || !d.model.trim())
    throw new Error('请填写任务名称并选择 Bot 和模型');
  if (d.taskName.trim().length > 128 || d.remark.length > 1000) throw new Error('任务名称最多128字，备注最多1000字');
  if (/\s/.test(d.model.trim())) throw new Error('模型标识不能包含空白');
  const common = {
    taskName: d.taskName.trim(),
    remark: d.remark.trim() || undefined,
    userId: d.ownerId,
    botId: d.botId,
    botEnv: d.botEnv,
    model: d.model.trim(),
    runtimeMaintenance: d.runtimeMaintenance,
  };
  if (['optimize', 'bench_optimize', 'full'].includes(d.type)) integer(d.maxRounds, 1, 100, '最大优化轮数');
  if (d.type === 'bench') {
    if (!d.benchDomainId) throw new Error('请选择包含已发布模板的 Domain');
    return {
      endpoint: 'benches',
      data: {
        ...common,
        benchDomainId: d.benchDomainId,
        benchDomainOwnerUserId: d.benchDomainOwnerUserId || d.ownerId,
        suite: 'all',
        scene: 'claw-evolve-bench',
      },
    };
  }
  if (d.type === 'bench_optimize') {
    if (!d.trainDomainId || !d.testDomainId || !d.objective.trim())
      throw new Error('请选择训练/测试 Domain 并填写优化目标');
    if (encodeURIComponent(d.objective.trim()).replace(/%[A-F\d]{2}/gi, 'x').length > 4096)
      throw new Error('优化目标最多4KiB');
    return {
      endpoint: 'bench-optimizations',
      data: {
        ...common,
        objective: d.objective.trim(),
        trainBenchDomainId: d.trainDomainId,
        trainBenchOwnerUserId: d.trainBenchOwnerUserId || d.ownerId,
        testBenchDomainId: d.testDomainId,
        testBenchOwnerUserId: d.testBenchOwnerUserId || d.ownerId,
        maxRounds: d.maxRounds,
      },
    };
  }
  if (d.type === 'optimize') {
    if (!d.sourceDiagnosisTaskId) throw new Error('请选择同 Bot 下已完成诊断和 Plan 的任务');
    return {
      endpoint: 'optimizations',
      data: { ...common, sourceDiagnosisTaskIds: [d.sourceDiagnosisTaskId], maxRounds: d.maxRounds },
    };
  }
  if (d.type === 'full') {
    if (!d.goal.trim()) throw new Error('请输入进化目标');
    if (d.goal.trim().length > 2000) throw new Error('进化目标最多2000字');
    return {
      endpoint: 'tasks',
      data: { ...common, taskType: 'full', inputMode: 'direct_goal', goal: d.goal.trim(), maxRounds: d.maxRounds },
    };
  }
  if (!d.focus.trim()) throw new Error('请输入关注问题');
  if (d.focus.length > 1000) throw new Error('关注问题最多1000字');
  const sessions = [
    ...new Set(
      d.sessionText
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
  if (d.explicitSessions) {
    if (sessions.length < 1 || sessions.length > 20) throw new Error('Session 标识必须是1到20个，每行一个');
  } else {
    if (!d.startDate || !d.endDate || d.startDate > d.endDate) throw new Error('请选择有效的会话时间范围');
    integer(d.maxSessions, 1, 1000, 'Session 上限');
    integer(d.badCases, 0, 100, 'Bad Case 数量');
    integer(d.goodCases, 0, 100, 'Good Case 数量');
    integer(d.badCases + d.goodCases, 1, 100, 'Good/Bad Case 总量');
  }
  const caseRequirement =
    d.badCases && d.goodCases
      ? `抽取${d.badCases}个 bad case 和${d.goodCases}个 good case`
      : d.badCases
      ? `抽取${d.badCases}个 bad case，不需要good case`
      : `抽取${d.goodCases}个 good case，不需要bad case`;
  const diagnoseIntent = d.explicitSessions
    ? `诊断指定的历史 session；重点关注${d.focus.trim()}。`
    : `扫描${d.startDate} 至 ${d.endDate}的历史 session；${caseRequirement}；重点关注${d.focus.trim()}。`;
  if (diagnoseIntent.length > 4000) throw new Error('诊断要求最多4000字');
  const diagnosis = {
    ...common,
    judgeBackend: 'subagent',
    sessionSource: 'local',
    diagnoseIntent,
    maxSessions: d.explicitSessions ? sessions.length : d.maxSessions,
    ...(d.explicitSessions
      ? { sessionFilter: { mode: 'explicit', sessionIdentifiers: sessions } }
      : { startDate: d.startDate, endDate: d.endDate }),
  };
  return { endpoint: 'diagnoses', data: diagnosis };
}

export function matchesDiagnosisTarget(task: Task, draft: CreateDraft) {
  const environment = String(task.config.botEnv ?? '');
  return (
    task.bot_id === draft.botId &&
    task.user_id === draft.ownerId &&
    (environment === draft.botEnv || (!environment && draft.unambiguousEnvironment === true))
  );
}

/** List responses may omit Plan output; validate the selected source with its detail response. */
export function validateDiagnosisSource(task: Task, draft: CreateDraft) {
  if (
    task.task_id !== draft.sourceDiagnosisTaskId ||
    task.config.targetSkillAssetId ||
    !matchesDiagnosisTarget(task, draft) ||
    !task.steps?.some((step) => step.stepType === 'diagnose' && step.status === 'succeeded')
  )
    throw new Error('请选择同 Bot 下已完成诊断和 Plan 的任务');
  const plan = task.steps?.filter((step) => step.stepType === 'plan' && step.status === 'succeeded').at(-1);
  const domains = plan?.output?.benchDomains as JsonObject | undefined;
  if (
    !domains ||
    typeof domains.trainBenchDomainId !== 'string' ||
    !domains.trainBenchDomainId.trim() ||
    typeof domains.testBenchDomainId !== 'string' ||
    !domains.testBenchDomainId.trim()
  )
    throw new Error('来源任务的 Plan 缺少训练/测试评测模板，请选择完整的诊断结果');
}
