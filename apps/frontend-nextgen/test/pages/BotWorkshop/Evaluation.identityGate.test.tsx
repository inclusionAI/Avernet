/** @jest-environment jsdom */
import EvaluationPage from '@/pages/BotWorkshop/Evaluation';
import { serviceBotEvalService } from '@/services/botWorkshop/serviceBotEvalService';
import '@testing-library/jest-dom';
import { act, render, screen, waitFor } from '@testing-library/react';

let mockIdentity = {
  ready: false,
  loading: true,
  error: undefined as string | undefined,
  userId: undefined as string | undefined,
};

jest.mock('@umijs/max', () => ({
  useParams: () => ({ botId: 'bot-1', botVersion: '8' }),
  useSearchParams: () => [new URLSearchParams('?owner_id=133525')],
  history: { push: jest.fn() },
}));

jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopRequestIdentity: () => mockIdentity,
}));

jest.mock('@/services/botWorkshop/serviceBotEvalService', () => ({
  serviceBotEvalService: {
    detail: jest.fn(),
    history: jest.fn(),
    submission: jest.fn(),
    submit: jest.fn(),
    sync: jest.fn(),
    precheck: jest.fn(),
  },
}));

const detail = jest.mocked(serviceBotEvalService.detail);
const loadHistory = jest.mocked(serviceBotEvalService.history);

beforeEach(() => {
  mockIdentity = { ready: false, loading: true, error: undefined, userId: undefined };
  jest.clearAllMocks();
  detail.mockResolvedValue({
    bot_id: 'bot-1',
    bot_version: '8',
    tabs: { simple_cert_submit: { visible: true } },
  });
  loadHistory.mockResolvedValue({ records: [], page: { page_no: 1, page_size: 10, total: 0 } });
});

it('身份未 ready 时不发起评测详情和历史请求', async () => {
  render(<EvaluationPage />);

  expect(screen.getByText('正在获取当前用户身份…')).toBeInTheDocument();
  await act(async () => {
    await Promise.resolve();
  });

  expect(detail).not.toHaveBeenCalled();
  expect(loadHistory).not.toHaveBeenCalled();
});

it('身份 ready 后才发起评测详情和历史请求', async () => {
  const { rerender } = render(<EvaluationPage />);

  expect(detail).not.toHaveBeenCalled();
  expect(loadHistory).not.toHaveBeenCalled();

  mockIdentity = {
    ready: true,
    loading: false,
    error: undefined,
    userId: '133525',
  };
  rerender(<EvaluationPage />);

  await waitFor(() => expect(detail).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(loadHistory).toHaveBeenCalledTimes(1));
  expect(detail).toHaveBeenCalledWith('bot-1', '8', '133525');
  expect(loadHistory).toHaveBeenCalledWith('bot-1', '8', '133525');
});

it('身份加载失败时展示错误并返回 Bot 管理', async () => {
  mockIdentity = {
    ready: false,
    loading: false,
    error: '当前用户身份加载失败',
    userId: undefined,
  };

  render(<EvaluationPage />);

  expect(screen.getByText('无法加载用户身份')).toBeInTheDocument();
  expect(screen.getByText('当前用户身份加载失败')).toBeInTheDocument();
  expect(detail).not.toHaveBeenCalled();
  expect(loadHistory).not.toHaveBeenCalled();
});
