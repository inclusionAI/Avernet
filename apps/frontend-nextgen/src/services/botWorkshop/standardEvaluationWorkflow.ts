import {
  standardEvaluationService,
  type StandardEvaluationGoalType,
  type StandardEvaluationSetItem,
  type StandardEnvironment,
  type StandardQualityTask,
} from './standardEvaluationService';

export type StandardEvaluationStepKey =
  | 'create_environment'
  | 'confirm_sample_set'
  | 'execute_evaluation'
  | 'evaluation_report'
  | 'save_evaluation_result';

export type StandardEvaluationStepStatus = 'pending' | 'running' | 'done' | 'failed';

export interface StandardEvaluationStep {
  key: StandardEvaluationStepKey;
  title: string;
  description: string;
}

export interface StandardEvaluationStepState extends StandardEvaluationStep {
  status: StandardEvaluationStepStatus;
  error?: string;
}

export const STANDARD_EVALUATION_STEPS: StandardEvaluationStep[] = [
  { key: 'create_environment', title: '创建评测环境', description: '基于 Eval 区服务 Bot 构建并部署评测环境。' },
  { key: 'confirm_sample_set', title: '确认评测样本集', description: '展示并确认用户已选定的 MASA 评测样本集。' },
  { key: 'execute_evaluation', title: '评测执行', description: 'TeamClaw 后端驱动 MASA 标准评测并同步任务进展。' },
  { key: 'evaluation_report', title: '评测报告', description: '展示本次 MASA 评测报告。' },
  { key: 'save_evaluation_result', title: '评测结果保存', description: '统一写入门禁结论，并在完成后由后端回收 Eval 区环境。' },
];

export const TERMINAL_TASK_STATUSES = new Set(['success', 'failed']);

export const EVALUATION_GOAL_INPUT_ENABLED = false;
// 长时评测不再设前端硬超时：错误态只跟随后端任务快照（FAILED/TIMEOUT 等）。
// 快照轮询放宽到 10s，避免小时级评测期间无效请求压力。
export const STANDARD_EVALUATION_POLL_MS = 10000;
// 环境构建发生在任务创建前（B5 异步构建），没有任务快照失败信号可跟随，保留兜底超时。
export const STANDARD_ENVIRONMENT_READY_TIMEOUT_MS = 30 * 60 * 1000;

export function createInitialEvaluationSteps(): StandardEvaluationStepState[] {
  return STANDARD_EVALUATION_STEPS.map((step) => ({ ...step, status: 'pending' as const, error: '' }));
}

function sleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function isFailedEnvironment(status: string): boolean {
  return ['failed', 'not_found'].includes(status);
}

export async function waitForStandardEnvironmentReady({
  botId,
  botVersion,
  ownerId,
  isAlive,
  onEnvironmentUpdate,
}: {
  botId: string;
  botVersion: string;
  ownerId?: string;
  isAlive: () => boolean;
  onEnvironmentUpdate: (environment: StandardEnvironment) => void;
}): Promise<StandardEnvironment> {
  let environment = await standardEvaluationService.createStandardEnvironment({
    botId,
    botVersion,
    ownerId,
  });
  if (!isAlive()) return environment;
  onEnvironmentUpdate(environment);

  if (environment.supported === false) return environment;
  const environmentDeadline = Date.now() + STANDARD_ENVIRONMENT_READY_TIMEOUT_MS;

  while (environment.build_status !== 'active') {
    if (isFailedEnvironment(environment.build_status)) {
      throw new Error(environment.error || '标准评测环境构建失败');
    }
    if (Date.now() >= environmentDeadline) {
      throw new Error('等待标准评测环境构建超时');
    }

    await sleep(STANDARD_EVALUATION_POLL_MS);
    environment = await standardEvaluationService.getStandardEnvironmentCurrent({
      botId,
      botVersion,
      ownerId,
    });
    if (!isAlive()) return environment;
    onEnvironmentUpdate(environment);
  }

  return environment;
}

export function getEvaluationTaskSaveStatus(task: StandardQualityTask): string | undefined {
  return task.steps?.find((step) => step.key === 'save_evaluation_result')?.status;
}

export function getEvaluationTaskStep(
  task: StandardQualityTask,
  key: StandardEvaluationStepKey,
): { status: string; error?: string } | undefined {
  return task.steps?.find((step) => step.key === key);
}

export function getEvaluationTaskError(task: StandardQualityTask): string {
  return task.error || task.ext?.error_msg || task.ext?.error || '';
}

export function getEvaluationReportUrl(task: StandardQualityTask): string {
  return task.report_url || task.ext?.eval_progress?.report?.reportUrl || '';
}

export function applyEvaluationTaskSteps(
  task: StandardQualityTask,
  updateStep: (key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus, error?: string) => void,
): void {
  task.steps?.forEach((step) => {
    const key = step.key as StandardEvaluationStepKey;
    const error = step.error || '';
    if (step.status === 'DONE') updateStep(key, 'done', error);
    if (step.status === 'FAILED') updateStep(key, 'failed', error);
    if (step.status === 'RUNNING') updateStep(key, 'running', error);
    if (step.status === 'WAITING') updateStep(key, 'pending', error);
  });
}

async function waitForEvaluationTaskSettled({
  initialTask,
  getTask,
  isAlive,
  onTaskUpdate,
}: {
  initialTask: StandardQualityTask;
  getTask: () => Promise<StandardQualityTask>;
  isAlive: () => boolean;
  onTaskUpdate: (task: StandardQualityTask) => void;
}): Promise<StandardQualityTask> {
  let task = initialTask;

  // 不设前端硬超时：评测可达小时级，等待仅由 isAlive（页面存活）与后端快照终态驱动。
  while (isAlive()) {
    onTaskUpdate(task);
    const saveStatus = getEvaluationTaskSaveStatus(task);
    if (saveStatus === 'DONE' || saveStatus === 'FAILED') return task;
    if (TERMINAL_TASK_STATUSES.has(task.status) && saveStatus === undefined) return task;

    await sleep(STANDARD_EVALUATION_POLL_MS);
    task = await getTask();
  }

  return task;
}

async function ensureStandardEnvironmentReleased({
  botId,
  botVersion,
  ownerId,
}: {
  botId: string;
  botVersion: string;
  ownerId?: string;
}): Promise<void> {
  const destroyResult = await standardEvaluationService.destroyStandardEnvironment({
    botId,
    botVersion,
    ownerId,
  });
  const environmentDestroyed =
    destroyResult.destroyed ||
    destroyResult.destroy_status === 'already_destroyed' ||
    destroyResult.destroy_status === 'succeeded';
  if (destroyResult.supported !== false && !environmentDestroyed) {
    throw new Error(destroyResult.error || '标准评测环境销毁失败');
  }
}

export async function settleStandardEvaluationTask({
  initialTask,
  botId,
  botVersion,
  ownerId,
  isAlive,
  onTaskUpdate,
}: {
  initialTask: StandardQualityTask;
  botId: string;
  botVersion: string;
  ownerId?: string;
  isAlive: () => boolean;
  onTaskUpdate: (task: StandardQualityTask) => void;
}): Promise<StandardQualityTask> {
  const task = await waitForEvaluationTaskSettled({
    initialTask,
    getTask: () =>
      standardEvaluationService.getStandardEvaluationCurrentTask({ botId, botVersion, ownerId }),
    isAlive,
    onTaskUpdate,
  });

  if (getEvaluationTaskSaveStatus(task) === 'DONE') {
    await ensureStandardEnvironmentReleased({ botId, botVersion, ownerId });
  }
  return task;
}

async function createEvaluationTask({
  botId,
  botVersion,
  ownerId,
  selectedSampleSet,
  goalType,
  goalText,
}: {
  botId: string;
  botVersion: string;
  ownerId?: string;
  selectedSampleSet: StandardEvaluationSetItem;
  goalType: StandardEvaluationGoalType;
  goalText: string;
}): Promise<StandardQualityTask> {
  return standardEvaluationService.createStandardEvaluationTask({
    botId,
    botVersion,
    ownerId,
    setUuid: selectedSampleSet.uuid,
    goalType,
    goalText,
    triggerSource: 'manual_retry_standard_eval',
  });
}

export interface StandardEvaluationStartInput {
  botId: string;
  botVersion: string;
  ownerId?: string;
  selectedSampleSet: StandardEvaluationSetItem;
  goalType: StandardEvaluationGoalType;
  goalText: string;
  isAlive: () => boolean;
  onStepUpdate: (key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus, error?: string) => void;
  onEnvironmentUpdate: (environment: StandardEnvironment) => void;
  onTaskUpdate: (task: StandardQualityTask) => void;
}

export async function startStandardEvaluationTask(
  input: StandardEvaluationStartInput,
): Promise<StandardQualityTask> {
  if (!input.selectedSampleSet) throw new Error('请选择评测样本集');
  input.onStepUpdate('create_environment', 'running');
  await waitForStandardEnvironmentReady({
    botId: input.botId,
    botVersion: input.botVersion,
    ownerId: input.ownerId,
    isAlive: input.isAlive,
    onEnvironmentUpdate: input.onEnvironmentUpdate,
  });
  input.onStepUpdate('create_environment', 'done');
  input.onStepUpdate('confirm_sample_set', 'running');
  input.onStepUpdate('confirm_sample_set', 'done');
  input.onStepUpdate('execute_evaluation', 'running');

  const initialTask = await createEvaluationTask(input);
  input.onTaskUpdate(initialTask);
  const task = await settleStandardEvaluationTask({
    initialTask,
    botId: input.botId,
    botVersion: input.botVersion,
    ownerId: input.ownerId,
    isAlive: input.isAlive,
    onTaskUpdate: input.onTaskUpdate,
  });

  const saveStatus = getEvaluationTaskSaveStatus(task);
  if (saveStatus === 'FAILED') {
    throw new Error(getEvaluationTaskError(task) || '评测结果保存失败');
  }
  if (saveStatus !== 'DONE') {
    // 未落终态：任务仍由服务端驱动，页面可用性或快照异常不应被包装成“超时失败”。
    throw new Error(getEvaluationTaskError(task) || '评测仍在后台执行，可稍后在评测记录中查看结果');
  }
  return task;
}

export interface StandardEvaluationStepRetryInput {
  step: StandardEvaluationStepKey;
  botId: string;
  botVersion: string;
  ownerId?: string;
  selectedSampleSet?: StandardEvaluationSetItem;
  goalType: StandardEvaluationGoalType;
  goalText: string;
  isAlive: () => boolean;
  onStepUpdate: (key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus, error?: string) => void;
  onEnvironmentUpdate: (environment: StandardEnvironment) => void;
  onTaskUpdate: (task: StandardQualityTask) => void;
}

export interface StandardEvaluationStepRetryResult {
  environment?: StandardEnvironment;
  task?: StandardQualityTask;
}

async function runEvaluationAndRelease(
  input: StandardEvaluationStepRetryInput,
  runningStep: StandardEvaluationStepKey,
): Promise<StandardQualityTask> {
  const selectedSampleSet = input.selectedSampleSet;
  if (!selectedSampleSet) throw new Error('请选择评测样本集');
  input.onStepUpdate(runningStep, 'running');
  const task = await createEvaluationTask({ ...input, selectedSampleSet });
  const settledTask = await settleStandardEvaluationTask({
    initialTask: task,
    botId: input.botId,
    botVersion: input.botVersion,
    ownerId: input.ownerId,
    isAlive: input.isAlive,
    onTaskUpdate: input.onTaskUpdate,
  });

  const saveStatus = getEvaluationTaskSaveStatus(settledTask);
  if (saveStatus === 'FAILED') {
    throw new Error(getEvaluationTaskError(settledTask) || '评测结果保存失败');
  }
  if (saveStatus !== 'DONE') {
    throw new Error(getEvaluationTaskError(settledTask) || '评测仍在后台执行，可稍后在评测记录中查看结果');
  }
  if (settledTask.status !== 'success') {
    throw new Error(getEvaluationTaskError(settledTask) || '评测未通过，已按 BLOCKED 保存门禁结果');
  }
  input.onStepUpdate(runningStep, 'done');
  return settledTask;
}

export async function retryStandardEvaluationStep(
  input: StandardEvaluationStepRetryInput,
): Promise<StandardEvaluationStepRetryResult> {
  const step = input.step;
  input.onStepUpdate(step, 'running');

  if (step === 'create_environment') {
    const environment = await waitForStandardEnvironmentReady({
      botId: input.botId,
      botVersion: input.botVersion,
      ownerId: input.ownerId,
      isAlive: input.isAlive,
      onEnvironmentUpdate: input.onEnvironmentUpdate,
    });
    input.onStepUpdate(step, 'done');
    return { environment };
  }

  if (step === 'confirm_sample_set') {
    if (!input.selectedSampleSet) throw new Error('请选择评测样本集');
    input.onStepUpdate(step, 'done');
    return {};
  }

  if (step === 'execute_evaluation') {
    return { task: await runEvaluationAndRelease(input, step) };
  }

  if (step === 'evaluation_report') {
    const task = await runEvaluationAndRelease(input, step);
    const reportUrl = getEvaluationReportUrl(task);
    if (!reportUrl) throw new Error('MASA未返回评测报告地址');
    input.onStepUpdate(step, 'done');
    return { task };
  }

  if (step === 'save_evaluation_result') {
    const currentTask = await standardEvaluationService.getStandardEvaluationCurrentTask({
      botId: input.botId,
      botVersion: input.botVersion,
      ownerId: input.ownerId,
    });
    const task = await settleStandardEvaluationTask({
      initialTask: currentTask,
      botId: input.botId,
      botVersion: input.botVersion,
      ownerId: input.ownerId,
      isAlive: input.isAlive,
      onTaskUpdate: input.onTaskUpdate,
    });
    const saveStatus = getEvaluationTaskSaveStatus(task);
    if (saveStatus !== 'DONE') {
      throw new Error(saveStatus === 'FAILED' ? '评测结果保存失败' : '等待标准评测完成超时');
    }
    input.onStepUpdate(step, 'done');
    return { task };
  }

  throw new Error('未知的评测流程节点');
}
