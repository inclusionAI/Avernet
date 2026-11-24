import { type StandardQualityTask, type StandardTaskEnvironment } from '@/services/botWorkshop/standardEvaluationService';
import {
  createInterruptedFlowResult,
  createRunningInBackgroundFlowResult,
  createStepRetrySucceededFlowResult,
  createTaskFlowResult,
  isBackgroundTaskMessage,
  markStepsInterrupted,
  resumeStepsAfter as resumeEvaluationStepsAfter,
  type StandardEvaluationFlowResult,
} from '@/services/botWorkshop/standardEvaluationFlowState';
import {
  applyEvaluationTaskSteps,
  createInitialEvaluationSteps,
  getEvaluationTaskError,
  retryStandardEvaluationStep,
  startStandardEvaluationTask,
  type StandardEvaluationStepKey,
  type StandardEvaluationStepStatus,
} from '@/services/botWorkshop/standardEvaluationWorkflow';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useStandardEvaluationGoal } from '@/hooks/useStandardEvaluationGoal';
import { useStandardEvaluationSampleSets } from '@/hooks/useStandardEvaluationSampleSets';

export type { StandardEvaluationFlowResult };

export interface StandardEvaluationFlowInput {
  botId: string;
  botVersion: string;
  ownerId?: string;
  enabled?: boolean;
  canStart?: boolean;
  onFinished?: () => void;
}

export function useStandardEvaluationFlow({
  botId,
  botVersion,
  ownerId,
  enabled = true,
  canStart = true,
  onFinished,
}: StandardEvaluationFlowInput) {
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState(createInitialEvaluationSteps);
  const [evaluationTask, setEvaluationTask] = useState<StandardQualityTask>();
  const [environment, setEnvironment] = useState<StandardTaskEnvironment>();
  const [error, setError] = useState<string>();
  const [retryingStep, setRetryingStep] = useState<StandardEvaluationStepKey>();
  const [flowResult, setFlowResult] = useState<StandardEvaluationFlowResult>();

  const currentStepKeyRef = useRef<StandardEvaluationStepKey>();
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const sampleSetState = useStandardEvaluationSampleSets({ botId, botVersion, ownerId, enabled });
  const goalState = useStandardEvaluationGoal();
  const { sampleSets, selectedSampleSetId, selectedSampleSet } = sampleSetState;
  const { goalType, goalPrompt, goalReady } = goalState;

  const updateStep = useCallback(
    (key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus, stepError?: string) => {
      if (status === 'running') currentStepKeyRef.current = key;
      setSteps((prev) => prev.map((step) => (step.key === key ? { ...step, status, error: stepError ?? '' } : step)));
    },
    [],
  );

  const resetSteps = useCallback(() => {
    currentStepKeyRef.current = 'create_environment';
    setSteps(createInitialEvaluationSteps());
  }, []);

  const markFlowInterrupted = useCallback((failureStep: StandardEvaluationStepKey | undefined, message: string) => {
    setSteps((prev) => markStepsInterrupted(prev, failureStep, message));
  }, []);

  const resumeStepsAfter = useCallback((stepKey: StandardEvaluationStepKey) => {
    setSteps((prev) => resumeEvaluationStepsAfter(prev, stepKey));
  }, []);

  const setTaskFlowResult = useCallback((task: StandardQualityTask) => {
    setFlowResult(createTaskFlowResult(task));
  }, []);

  const updateEvaluationTask = useCallback(
    (task: StandardQualityTask) => {
      setEvaluationTask(task);
      if (task.environment) setEnvironment(task.environment);
      applyEvaluationTaskSteps(task, updateStep);
    },
    [updateStep],
  );

  const startEvaluation = useCallback(async () => {
    if (!enabled || !canStart || running || !selectedSampleSetId || !goalReady) return;

    const selectedSet = sampleSets.find((item) => item.uuid === selectedSampleSetId);
    if (!selectedSet) {
      setError('所选评测样本集不存在');
      return;
    }

    resetSteps();
    setRunning(true);
    setRetryingStep(undefined);
    setError(undefined);
    setEvaluationTask(undefined);
    setEnvironment(undefined);
    setFlowResult(undefined);

    try {
      const task = await startStandardEvaluationTask({
        botId,
        botVersion,
        ownerId,
        selectedSampleSet: selectedSet,
        goalType,
        goalText: goalType === 'semantic' ? goalPrompt : '',
        isAlive: () => aliveRef.current,
        onStepUpdate: updateStep,
        onEnvironmentUpdate: setEnvironment,
        onTaskUpdate: updateEvaluationTask,
      });
      if (!aliveRef.current) return;

      if (task.status !== 'success') {
        setError(getEvaluationTaskError(task) || '评测未通过，已按 BLOCKED 保存门禁结果');
      }
      setTaskFlowResult(task);
      onFinished?.();
    } catch (cause) {
      const failureStep = currentStepKeyRef.current;
      const message = cause instanceof Error ? cause.message : '重新发起评测失败';
      markFlowInterrupted(failureStep, message);
      setFlowResult(
        isBackgroundTaskMessage(message)
          ? createRunningInBackgroundFlowResult(message)
          : createInterruptedFlowResult(failureStep, message),
      );
      setError(message);
    } finally {
      setRetryingStep(undefined);
      setRunning(false);
    }
  }, [
    botId,
    botVersion,
    canStart,
    enabled,
    goalPrompt,
    goalReady,
    goalType,
    onFinished,
    ownerId,
    resetSteps,
    running,
    sampleSets,
    selectedSampleSetId,
    updateEvaluationTask,
    markFlowInterrupted,
    setTaskFlowResult,
    updateStep,
  ]);

  const retryStep = useCallback(
    async (key: StandardEvaluationStepKey) => {
      if (!enabled || !canStart || running) return;

      setRetryingStep(key);
      setRunning(true);
      setError(undefined);
      setFlowResult(undefined);
      try {
        const result = await retryStandardEvaluationStep({
          step: key,
          botId,
          botVersion,
          ownerId,
          selectedSampleSet,
          goalType,
          goalText: goalType === 'semantic' ? goalPrompt : '',
          isAlive: () => aliveRef.current,
          onStepUpdate: updateStep,
          onEnvironmentUpdate: setEnvironment,
          onTaskUpdate: updateEvaluationTask,
        });

        if (result.task) {
          setTaskFlowResult(result.task);
        } else {
          resumeStepsAfter(key);
          setFlowResult(createStepRetrySucceededFlowResult(key));
        }
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : '节点重试失败';
        markFlowInterrupted(key, message);
        setFlowResult(
          isBackgroundTaskMessage(message)
            ? createRunningInBackgroundFlowResult(message)
            : createInterruptedFlowResult(key, message),
        );
        setError(message);
      } finally {
        setRetryingStep(undefined);
        setRunning(false);
      }
    },
    [
      botId,
      botVersion,
      canStart,
      enabled,
      goalPrompt,
      goalType,
      ownerId,
      running,
      selectedSampleSet,
      markFlowInterrupted,
      resumeStepsAfter,
      setTaskFlowResult,
      updateEvaluationTask,
      updateStep,
    ],
  );

  const reportUrl = evaluationTask?.report_url || evaluationTask?.ext?.eval_progress?.report?.reportUrl;

  return {
    ...sampleSetState,
    ...goalState,
    running,
    steps,
    environment,
    evaluationTask,
    reportUrl,
    flowResult,
    error,
    startEvaluation,
    retryStep,
    retryingStep,
  };
}
