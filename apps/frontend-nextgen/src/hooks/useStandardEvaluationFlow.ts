import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  standardEvaluationService,
  type StandardEvaluationGoalType,
  type StandardEvaluationSetItem,
  type StandardQualityTask,
  type StandardTaskEnvironment,
} from '@/services/botWorkshop/standardEvaluationService';

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

const STANDARD_EVALUATION_STEPS: StandardEvaluationStep[] = [
  { key: 'create_environment', title: '创建评测环境', description: '基于 Eval 区服务 Bot 构建并部署评测环境。' },
  { key: 'confirm_sample_set', title: '确认评测样本集', description: '展示并确认用户已选定的 MASA 评测样本集。' },
  { key: 'execute_evaluation', title: '评测执行', description: 'TeamClaw 后端驱动 MASA 标准评测并同步任务进展。' },
  { key: 'evaluation_report', title: '评测报告', description: '展示本次 MASA 评测报告。' },
  { key: 'save_evaluation_result', title: '评测结果保存', description: '统一写入门禁结论，并在完成后由后端回收 Eval 区环境。' },
];

const TERMINAL_TASK_STATUSES = new Set(['success', 'failed']);

/** 评测目标输入待 MASA 侧现化后开放；关闭时不阻塞标准评测发起。 */
export const EVALUATION_GOAL_INPUT_ENABLED = false;

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
  const [steps, setSteps] = useState<StandardEvaluationStepState[]>(
    STANDARD_EVALUATION_STEPS.map((step) => ({ ...step, status: 'pending' as const })),
  );
  const [evaluationTask, setEvaluationTask] = useState<StandardQualityTask>();
  const [environment, setEnvironment] = useState<StandardTaskEnvironment>();
  const [error, setError] = useState<string>();

  const currentStepKeyRef = useRef<StandardEvaluationStepKey>();
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const updateStep = useCallback((key: StandardEvaluationStepKey, status: StandardEvaluationStepStatus) => {
    if (status === 'running') currentStepKeyRef.current = key;
    setSteps((prev) => prev.map((step) => (step.key === key ? { ...step, status } : step)));
  }, []);

  const resetSteps = useCallback(() => {
    currentStepKeyRef.current = 'create_environment';
    setSteps(STANDARD_EVALUATION_STEPS.map((step) => ({ ...step, status: 'pending' as const })));
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

  const startEvaluation = useCallback(async () => {
    if (!enabled || !canStart || running || !selectedSampleSetId || !goalReady) return;

    resetSteps();
    setRunning(true);
    setError(undefined);
    setEvaluationTask(undefined);
    setEnvironment(undefined);

    try {
      const selectedSet = sampleSets.find((item) => item.uuid === selectedSampleSetId);
      if (!selectedSet) throw new Error('所选评测样本集不存在');

      updateStep('create_environment', 'running');
      let standardEnvironment = await standardEvaluationService.createStandardEnvironment({
        botId,
        botVersion,
        ownerId,
      });
      if (!aliveRef.current) return;
      setEnvironment(standardEnvironment);

      const environmentDeadline = Date.now() + 30 * 60 * 1000;
      if (standardEnvironment.supported !== false && standardEnvironment.build_status !== 'active') {
        while (Date.now() < environmentDeadline) {
          if (['failed', 'not_found'].includes(standardEnvironment.build_status)) {
            throw new Error(standardEnvironment.error || '标准评测环境构建失败');
          }

          await new Promise<void>((resolve) => {
            window.setTimeout(resolve, 3000);
          });
          standardEnvironment = await standardEvaluationService.getStandardEnvironmentCurrent({
            botId,
            botVersion,
            ownerId,
          });
          if (!aliveRef.current) return;
          setEnvironment(standardEnvironment);
          if (standardEnvironment.build_status === 'active') break;
        }

        if (standardEnvironment.supported !== false && standardEnvironment.build_status !== 'active') {
          throw new Error('等待标准评测环境构建超时');
        }
      } else if (
        standardEnvironment.supported !== false &&
        ['failed', 'not_found'].includes(standardEnvironment.build_status)
      ) {
        throw new Error(standardEnvironment.error || '标准评测环境构建失败');
      }
      updateStep('create_environment', 'done');

      updateStep('confirm_sample_set', 'running');
      updateStep('confirm_sample_set', 'done');

      updateStep('execute_evaluation', 'running');
      let task = await standardEvaluationService.createStandardEvaluationTask({
        botId,
        botVersion,
        ownerId,
        setUuid: selectedSet.uuid,
        goalType,
        goalText: goalType === 'semantic' ? goalPrompt : '',
      });
      if (!aliveRef.current) return;
      setEvaluationTask(task);
      if (task.environment) setEnvironment(task.environment);

      const taskDeadline = Date.now() + 30 * 60 * 1000;
      let currentSaveStatus: string | undefined;
      while (Date.now() < taskDeadline) {
        const saveStep = task.steps?.find((step) => step.key === 'save_evaluation_result');
        currentSaveStatus = saveStep?.status;

        if (currentSaveStatus === 'DONE') break;
        if (currentSaveStatus === 'FAILED') break;
        if (TERMINAL_TASK_STATUSES.has(task.status) && currentSaveStatus === undefined) break;

        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, 3000);
        });
        task = await standardEvaluationService.getStandardEvaluationCurrentTask({
          botId,
          botVersion,
          ownerId,
        });
        if (!aliveRef.current) return;
        setEvaluationTask(task);
        if (task.environment) setEnvironment(task.environment);
        task.steps?.forEach((step) => {
          if (step.status === 'DONE') updateStep(step.key as StandardEvaluationStepKey, 'done');
          if (step.status === 'FAILED') updateStep(step.key as StandardEvaluationStepKey, 'failed');
          if (step.status === 'RUNNING') updateStep(step.key as StandardEvaluationStepKey, 'running');
          if (step.status === 'WAITING') updateStep(step.key as StandardEvaluationStepKey, 'pending');
        });
      }

      if (currentSaveStatus !== 'DONE') {
        if (currentSaveStatus === 'FAILED') {
          throw new Error(task.error || '标准评测流程失败');
        }
        throw new Error('等待标准评测完成超时');
      }

      updateStep('save_evaluation_result', 'running');
      const destroyResult = await standardEvaluationService.destroyStandardEnvironment({
        botId,
        botVersion,
        ownerId,
      });
      if (!aliveRef.current) return;
      const environmentDestroyed =
        destroyResult.destroyed || destroyResult.destroy_status === 'already_destroyed';
      if (destroyResult.supported !== false && !environmentDestroyed) {
        throw new Error(destroyResult.error || '标准评测环境销毁失败');
      }
      updateStep('save_evaluation_result', 'done');

      if (task.status !== 'success') {
        setError(task.error || task.ext?.error_msg || task.ext?.error || '评测未通过，已按 BLOCKED 保存门禁结果');
      }
      onFinished?.();
    } catch (cause) {
      const failureStep = currentStepKeyRef.current;
      if (failureStep) updateStep(failureStep, 'failed');
      setError(cause instanceof Error ? cause.message : '重新发起评测失败');
    } finally {
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
    updateStep,
  ]);

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
  };
}
