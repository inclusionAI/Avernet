import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  standardEvaluationService,
  type StandardEvaluationGoalType, type StandardEvaluationSetItem,
  type StandardQualityTask,
  type StandardTaskEnvironment,
} from '@/services/botWorkshop/standardEvaluationService';
import {
  applyEvaluationTaskSteps,
  createInitialEvaluationSteps,
  EVALUATION_GOAL_INPUT_ENABLED,
  getEvaluationTaskError,
  retryStandardEvaluationStep,
  startStandardEvaluationTask,
  type StandardEvaluationStepKey,
  type StandardEvaluationStepStatus,
} from '@/services/botWorkshop/standardEvaluationWorkflow';

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
  const [sampleSets, setSampleSets] = useState<StandardEvaluationSetItem[]>([]);
  const [sampleSetLoading, setSampleSetLoading] = useState(false);
  const [sampleSetError, setSampleSetError] = useState<string>();
  const [selectedSampleSetId, setSelectedSampleSetId] = useState<string>();
  const [goalType, setGoalType] = useState<StandardEvaluationGoalType>('json');
  const [goalFile, setGoalFile] = useState<File>();
  const [goalPrompt, setGoalPrompt] = useState('');
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState(createInitialEvaluationSteps);
  const [evaluationTask, setEvaluationTask] = useState<StandardQualityTask>();
  const [environment, setEnvironment] = useState<StandardTaskEnvironment>();
  const [error, setError] = useState<string>();
  const [retryingStep, setRetryingStep] = useState<StandardEvaluationStepKey>();

  const currentStepKeyRef = useRef<StandardEvaluationStepKey>();
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

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

  const loadSampleSets = useCallback(async () => {
    if (!botId || !enabled) return;
    setSampleSetLoading(true);
    setSampleSetError(undefined);
    try {
      const result = await standardEvaluationService.listEvaluationSets(botId, botVersion, ownerId);
      if (!aliveRef.current) return;
      setSampleSets(result.sets || []);
      setSelectedSampleSetId((current) => current || result.sets?.[0]?.uuid || undefined);
    } catch (cause) {
      if (!aliveRef.current) return;
      setSampleSets([]);
      setSelectedSampleSetId(undefined);
      setSampleSetError(cause instanceof Error ? cause.message : '获取评测样本集失败');
    } finally {
      if (aliveRef.current) setSampleSetLoading(false);
    }
  }, [botId, botVersion, enabled, ownerId]);

  useEffect(() => {
    void loadSampleSets();
  }, [loadSampleSets]);

  const selectedSampleSet = useMemo(
    () => sampleSets.find((item) => item.uuid === selectedSampleSetId),
    [sampleSets, selectedSampleSetId],
  );

  const goalReady = EVALUATION_GOAL_INPUT_ENABLED
    ? goalType === 'json'
      ? Boolean(goalFile)
      : Boolean(goalPrompt.trim())
    : true;

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
      onFinished?.();
    } catch (cause) {
      const failureStep = currentStepKeyRef.current;
      const message = cause instanceof Error ? cause.message : '重新发起评测失败';
      if (failureStep) updateStep(failureStep, 'failed', message);
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
    updateStep,
  ]);

  const retryStep = useCallback(
    async (key: StandardEvaluationStepKey) => {
      if (!enabled || !canStart || running) return;

      setRetryingStep(key);
      setRunning(true);
      setError(undefined);
      try {
        await retryStandardEvaluationStep({
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
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : '节点重试失败';
        updateStep(key, 'failed', message);
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
      updateEvaluationTask,
      updateStep,
    ],
  );

  const reportUrl = evaluationTask?.report_url || evaluationTask?.ext?.eval_progress?.report?.reportUrl;

  return {
    sampleSets,
    sampleSetLoading,
    sampleSetError,
    selectedSampleSetId,
    selectedSampleSet,
    setSelectedSampleSetId,
    reloadSampleSets: loadSampleSets,
    goalType,
    setGoalType,
    goalFile,
    setGoalFile,
    goalPrompt,
    setGoalPrompt,
    goalReady,
    running,
    steps,
    environment,
    evaluationTask,
    reportUrl,
    error,
    startEvaluation,
    retryStep,
    retryingStep,
  };
}
