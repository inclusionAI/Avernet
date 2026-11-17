import {
  standardEvaluationService,
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
}

export const STANDARD_EVALUATION_STEPS: StandardEvaluationStep[] = [
  { key: 'create_environment', title: '创建评测环境', description: '基于 Eval 区服务 Bot 构建并部署评测环境。' },
  { key: 'confirm_sample_set', title: '确认评测样本集', description: '展示并确认用户已选定的 MASA 评测样本集。' },
  { key: 'execute_evaluation', title: '评测执行', description: 'TeamClaw 后端驱动 MASA 标准评测并同步任务进展。' },
  { key: 'evaluation_report', title: '评测报告', description: '展示本次 MASA 评测报告。' },
  { key: 'save_evaluation_result', title: '评测结果保存', description: '统一写入门禁结论，并在完成后由后端回收 Eval 区环境。' },
];

export const TERMINAL_TASK_STATUSES = new Set(['success', 'failed']);

/** 评测目标输入待 MASA 侧现化后开放；关闭时不阻塞标准评测发起。 */
export const EVALUATION_GOAL_INPUT_ENABLED = false;

export function createInitialEvaluationSteps(): StandardEvaluationStepState[] {
  return STANDARD_EVALUATION_STEPS.map((step) => ({ ...step, status: 'pending' as const }));
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
  const environmentDeadline = Date.now() + 30 * 60 * 1000;

  while (environment.build_status !== 'active') {
    if (isFailedEnvironment(environment.build_status)) {
      throw new Error(environment.error || '标准评测环境构建失败');
    }
    if (Date.now() >= environmentDeadline) {
      throw new Error('等待标准评测环境构建超时');
    }

    await sleep(3000);
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

export function applyEvaluationTaskSteps(
  task: StandardQualityTask,
  updateStep: (key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus) => void,
): void {
  task.steps?.forEach((step) => {
    const key = step.key as StandardEvaluationStepKey;
    if (step.status === 'DONE') updateStep(key, 'done');
    if (step.status === 'FAILED') updateStep(key, 'failed');
    if (step.status === 'RUNNING') updateStep(key, 'running');
    if (step.status === 'WAITING') updateStep(key, 'pending');
  });
}
