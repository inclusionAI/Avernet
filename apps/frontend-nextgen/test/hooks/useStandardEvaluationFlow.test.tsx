/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';

import { useStandardEvaluationFlow } from '@/hooks/useStandardEvaluationFlow';
import { standardEvaluationService, type StandardQualityTask } from '@/services/botWorkshop/standardEvaluationService';
import { startStandardEvaluationTask } from '@/services/botWorkshop/standardEvaluationWorkflow';

jest.mock('@/services/botWorkshop/standardEvaluationService', () => ({
  standardEvaluationService: {
    listEvaluationSets: jest.fn().mockResolvedValue({
      support: true,
      sets: [
        {
          uuid: 'set-1',
          name: '默认样本集',
          sample_count: 10,
        },
      ],
    }),
  },
}));

jest.mock('@/services/botWorkshop/standardEvaluationWorkflow', () => ({
  ...jest.requireActual('@/services/botWorkshop/standardEvaluationWorkflow'),
  startStandardEvaluationTask: jest.fn(),
  retryStandardEvaluationStep: jest.fn(),
}));

const mockedStart = startStandardEvaluationTask as jest.Mock;
const mockedService = standardEvaluationService as unknown as { listEvaluationSets: jest.Mock };

function renderFlow(onFinished?: () => void) {
  return renderHook(() =>
    useStandardEvaluationFlow({
      botId: '20260828_r3fgibtt',
      botVersion: '9',
      ownerId: '133525',
      onFinished,
    }),
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test('门禁保存完成后，即使评测未通过也按完成但未通过展示', async () => {
  const onFinished = jest.fn();
  const task = {
    id: 1,
    status: 'failed',
    steps: [{ key: 'save_evaluation_result', status: 'DONE' }],
  } as StandardQualityTask;
  mockedStart.mockResolvedValueOnce(task);

  const { result } = renderFlow(onFinished);
  await waitFor(() => expect(result.current.selectedSampleSetId).toBe('set-1'));

  await act(async () => {
    await result.current.startEvaluation();
  });

  expect(mockedService.listEvaluationSets).toHaveBeenCalledTimes(1);
  expect(result.current.running).toBe(false);
  expect(result.current.flowResult).toMatchObject({ kind: 'completed_but_not_passed' });
  expect(onFinished).toHaveBeenCalledTimes(1);
});

test('编排节点失败时标记失败节点和后续未执行节点', async () => {
  mockedStart.mockRejectedValueOnce(new Error('标准评测环境构建失败'));

  const { result } = renderFlow();
  await waitFor(() => expect(result.current.selectedSampleSetId).toBe('set-1'));

  await act(async () => {
    await result.current.startEvaluation();
  });

  expect(result.current.flowResult).toMatchObject({
    kind: 'interrupted',
    stepKey: 'create_environment',
    stepTitle: '创建评测环境',
    message: '标准评测环境构建失败',
  });
  expect(result.current.error).toBe('标准评测环境构建失败');
  expect(result.current.steps.map((step) => step.status)).toEqual([
    'failed',
    'skipped',
    'skipped',
    'skipped',
    'skipped',
  ]);
});
