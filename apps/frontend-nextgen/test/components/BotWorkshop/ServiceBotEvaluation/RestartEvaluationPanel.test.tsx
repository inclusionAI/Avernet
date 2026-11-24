/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

import { RestartEvaluationPanel } from '@/components/BotWorkshop/ServiceBotEvaluation/RestartEvaluationPanel';
import { notifyError, notifySuccess, notifyWarning } from '@/components/ui/notify';
import { useStandardEvaluationFlow } from '@/hooks/useStandardEvaluationFlow';

jest.mock('@/components/ui/notify', () => ({
  notifyError: jest.fn(),
  notifySuccess: jest.fn(),
  notifyWarning: jest.fn(),
}));

jest.mock('@/hooks/useStandardEvaluationFlow', () => ({
  EVALUATION_GOAL_INPUT_ENABLED: false,
  useStandardEvaluationFlow: jest.fn(),
}));

const mockedUseStandardEvaluationFlow = useStandardEvaluationFlow as jest.MockedFunction<
  typeof useStandardEvaluationFlow
>;
const mockedNotifyError = notifyError as unknown as jest.Mock;
const mockedNotifySuccess = notifySuccess as jest.Mock;
const mockedNotifyWarning = notifyWarning as jest.Mock;

beforeEach(() => {
  mockedNotifyError.mockClear();
  mockedNotifySuccess.mockClear();
  mockedNotifyWarning.mockClear();

  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
});

function mockFlow(overrides: Partial<ReturnType<typeof useStandardEvaluationFlow>> = {}) {
  const base: ReturnType<typeof useStandardEvaluationFlow> = {
    sampleSets: [],
    sampleSetLoading: false,
    sampleSetError: undefined,
    selectedSampleSetId: undefined,
    selectedSampleSet: undefined,
    setSelectedSampleSetId: jest.fn(),
    reloadSampleSets: jest.fn(),
    goalType: 'json',
    setGoalType: jest.fn(),
    goalFile: undefined,
    setGoalFile: jest.fn(),
    goalPrompt: '',
    setGoalPrompt: jest.fn(),
    goalReady: true,
    running: false,
    steps: [
      { key: 'create_environment', title: '创建评测环境', description: '创建评测环境', status: 'pending' },
      { key: 'confirm_sample_set', title: '确认评测样本集', description: '确认评测样本集', status: 'pending' },
      { key: 'execute_evaluation', title: '评测执行', description: '评测执行', status: 'pending' },
      { key: 'evaluation_report', title: '评测报告', description: '评测报告', status: 'pending' },
      { key: 'save_evaluation_result', title: '评测结果保存', description: '评测结果保存', status: 'pending' },
    ],
    environment: undefined,
    evaluationTask: undefined,
    reportUrl: undefined,
    flowResult: undefined,
    error: undefined,
    startEvaluation: jest.fn(),
    retryStep: jest.fn(),
    retryingStep: undefined,
  };
  mockedUseStandardEvaluationFlow.mockReturnValue({ ...base, ...overrides });
}

test('样本集为空时引导用户前往 MASA 注册 Eval 区智能体', () => {
  mockFlow();

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.getByText('暂未获取到评测样本集。')).toBeInTheDocument();
  expect(screen.getByText(/请前往 MASA 平台智能体评测专区完成智能体注册/)).toBeInTheDocument();
  expect(screen.getByText('【评测环境 Eval 区】')).toBeInTheDocument();

  const guideLink = screen.getByRole('link', { name: /前往 MASA 智能体评测专区/ });
  expect(guideLink).toHaveAttribute('href', 'https://masa.alipay.com/evaluation/agent/projects');
  expect(guideLink).toHaveAttribute('target', '_blank');
  expect(guideLink).toHaveAttribute('rel', 'noopener noreferrer');
  expect(screen.getByRole('button', { name: /重新获取样本集/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /开始评测/ })).toBeDisabled();
});

test('样本集获取失败时展示转译文案并引导注册', () => {
  mockFlow({ sampleSetError: '获取评测样本集失败：未找到对应的 MASA 评测智能体' });

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.getByText('获取评测样本集失败：未找到对应的 MASA 评测智能体')).toBeInTheDocument();
  expect(screen.queryByText('Not Found')).not.toBeInTheDocument();
  expect(screen.getByText('【评测环境 Eval 区】')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /前往 MASA 智能体评测专区/ })).toBeInTheDocument();
});

test('评测目标输入暂不展示且不阻塞开始评测', () => {
  mockFlow({
    sampleSets: [
      {
        uuid: 'set-1',
        name: '默认样本集',
        sample_count: 10,
        sample_set_url: 'https://masa.example.com/set-1',
      },
    ],
    selectedSampleSetId: 'set-1',
    selectedSampleSet: {
      uuid: 'set-1',
      name: '默认样本集',
      sample_count: 10,
      sample_set_url: 'https://masa.example.com/set-1',
    },
  });

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.queryByText('评测目标格式')).not.toBeInTheDocument();
  expect(screen.queryByText('评测目标文件')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /开始评测/ })).not.toBeDisabled();
  expect(screen.queryByText(/暂未获取到评测样本集/)).not.toBeInTheDocument();
});

test('评测完成且通过时展示成功终态和报告入口', () => {
  mockFlow({
    sampleSets: [{ uuid: 'set-1', name: '默认样本集', sample_count: 10 }],
    selectedSampleSetId: 'set-1',
    selectedSampleSet: { uuid: 'set-1', name: '默认样本集', sample_count: 10 },
    reportUrl: 'https://masa.example.com/report',
    flowResult: { kind: 'success', message: '评测结果已通过，本次评测记录已保存。' },
  });

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.getByRole('status')).toHaveTextContent('评测流程已完成');
  expect(screen.getByText('评测结果已通过，本次评测记录已保存。')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /查看评测报告/ })).toHaveAttribute('href', 'https://masa.example.com/report');
  expect(mockedNotifySuccess).toHaveBeenCalledWith('评测流程已完成');
});

test('评测完成但未通过时展示警示终态', () => {
  mockFlow({
    sampleSets: [{ uuid: 'set-1', name: '默认样本集', sample_count: 10 }],
    selectedSampleSetId: 'set-1',
    selectedSampleSet: { uuid: 'set-1', name: '默认样本集', sample_count: 10 },
    flowResult: {
      kind: 'completed_but_not_passed',
      message: '评测结果未通过，已按 BLOCKED 保存门禁结果。',
    },
  });

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.getByRole('status')).toHaveTextContent('评测流程已完成，但结果未通过');
  expect(screen.getByText('评测结果未通过，已按 BLOCKED 保存门禁结果。')).toBeInTheDocument();
  expect(mockedNotifyWarning).toHaveBeenCalledWith('评测完成，但结果未通过');
});

test('节点失败时展示失败终态并将后续节点标记为未执行', () => {
  mockFlow({
    sampleSets: [{ uuid: 'set-1', name: '默认样本集', sample_count: 10 }],
    selectedSampleSetId: 'set-1',
    selectedSampleSet: { uuid: 'set-1', name: '默认样本集', sample_count: 10 },
    steps: [
      { key: 'create_environment', title: '创建评测环境', description: '创建评测环境', status: 'done' },
      {
        key: 'confirm_sample_set',
        title: '确认评测样本集',
        description: '确认评测样本集',
        status: 'failed',
        error: '标准评测环境构建失败',
      },
      { key: 'execute_evaluation', title: '评测执行', description: '评测执行', status: 'skipped' },
      { key: 'evaluation_report', title: '评测报告', description: '评测报告', status: 'skipped' },
      { key: 'save_evaluation_result', title: '评测结果保存', description: '评测结果保存', status: 'skipped' },
    ],
    error: '标准评测环境构建失败',
    flowResult: {
      kind: 'interrupted',
      stepKey: 'confirm_sample_set',
      stepTitle: '确认评测样本集',
      message: '标准评测环境构建失败',
    },
  });

  render(<RestartEvaluationPanel botId="20260828_r3fgibtt" botVersion="9" ownerId="133525" />);

  expect(screen.getByRole('status')).toHaveTextContent('评测流程中断');
  expect(screen.getByText(/确认评测样本集执行失败。标准评测环境构建失败/)).toBeInTheDocument();
  expect(screen.getAllByText('未执行').length).toBe(3);
  expect(mockedNotifyError).toHaveBeenCalledWith('标准评测环境构建失败', { title: '评测流程失败' });
});
