import type { StandardQualityTask } from './standardEvaluationService';
import {
  getEvaluationTaskError,
  STANDARD_EVALUATION_STEPS,
  type StandardEvaluationStepKey,
  type StandardEvaluationStepState,
} from './standardEvaluationWorkflow';

export type StandardEvaluationFlowResultKind =
  | 'success'
  | 'completed_but_not_passed'
  | 'interrupted'
  | 'running_in_background'
  | 'step_retry_succeeded';

export interface StandardEvaluationFlowResult {
  kind: StandardEvaluationFlowResultKind;
  message?: string;
  stepKey?: StandardEvaluationStepKey;
  stepTitle?: string;
}

const getStepTitle = (stepKey?: StandardEvaluationStepKey): string =>
  STANDARD_EVALUATION_STEPS.find((step) => step.key === stepKey)?.title ?? '';

export const isBackgroundTaskMessage = (message: string): boolean =>
  message.includes('后台执行');

export function createTaskFlowResult(task: StandardQualityTask): StandardEvaluationFlowResult {
  if (task.status === 'success') {
    return {
      kind: 'success',
      message: '评测结果已通过，本次评测记录已保存。',
    };
  }

  return {
    kind: 'completed_but_not_passed',
    message: getEvaluationTaskError(task) || '评测结果未通过，已按 BLOCKED 保存门禁结果。',
  };
}

export function createRunningInBackgroundFlowResult(message: string): StandardEvaluationFlowResult {
  return {
    kind: 'running_in_background',
    message,
  };
}

export function createInterruptedFlowResult(
  stepKey: StandardEvaluationStepKey | undefined,
  message: string,
): StandardEvaluationFlowResult {
  return {
    kind: 'interrupted',
    stepKey,
    stepTitle: getStepTitle(stepKey),
    message,
  };
}

export function createStepRetrySucceededFlowResult(
  stepKey: StandardEvaluationStepKey,
): StandardEvaluationFlowResult {
  return {
    kind: 'step_retry_succeeded',
    stepKey,
    stepTitle: getStepTitle(stepKey),
    message: '节点已重试成功，请继续执行后续节点。',
  };
}

export function markStepsInterrupted(
  steps: StandardEvaluationStepState[],
  failureStep: StandardEvaluationStepKey | undefined,
  message: string,
): StandardEvaluationStepState[] {
  const failedIndex = steps.findIndex((step) => step.key === failureStep);
  if (failedIndex === -1) return steps;

  return steps.map((step, index) => {
    if (index === failedIndex) return { ...step, status: 'failed' as const, error: message };
    if (index > failedIndex && step.status === 'pending') {
      return { ...step, status: 'skipped' as const, error: '' };
    }
    return step;
  });
}

export function resumeStepsAfter(
  steps: StandardEvaluationStepState[],
  stepKey: StandardEvaluationStepKey,
): StandardEvaluationStepState[] {
  const recoveredIndex = steps.findIndex((step) => step.key === stepKey);
  if (recoveredIndex === -1) return steps;

  return steps.map((step, index) =>
    index > recoveredIndex && step.status === 'skipped'
      ? { ...step, status: 'pending' as const, error: '' }
      : step,
  );
}
