/** @jest-environment jsdom */
import LabLandingPage from '@/pages/Lab';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { history } from '@umijs/max';
import { toast } from 'sonner';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: () => ({ pathname: '/lab' }),
}));
jest.mock('sonner', () => ({
  toast: { info: jest.fn(), success: jest.fn(), error: jest.fn() },
}));

const push = jest.mocked(history.push);
const toastInfo = jest.mocked(toast.info);

describe('LabLandingPage', () => {
  beforeEach(() => {
    push.mockClear();
    toastInfo.mockClear();
  });

  test('渲染实验室落地页：hero 标题 + 单张社区功能卡（齿轮配置 + 开始体验 + 我要反馈）', () => {
    render(<LabLandingPage />);
    expect(screen.getByRole('heading', { name: '实验室' })).toBeInTheDocument();
    const cards = screen.getAllByRole('article');
    expect(cards).toHaveLength(1);
    const card = within(cards[0]);
    expect(card.getByRole('heading', { name: '社区' })).toBeInTheDocument();
    // 齿轮「Bot 访问社区配置」位于社区卡右上角（进入社区前可见）。
    expect(card.getByRole('button', { name: 'Bot 访问社区配置' })).toBeInTheDocument();
    expect(card.getByRole('button', { name: '开始体验' })).toBeInTheDocument();
    expect(card.getByRole('button', { name: /我要反馈/ })).toBeInTheDocument();
    // 暂不展示任务卡。
    expect(screen.queryByRole('heading', { name: '任务' })).not.toBeInTheDocument();
  });

  test('点社区「开始体验」跳 /lab/community', () => {
    render(<LabLandingPage />);
    fireEvent.click(screen.getByRole('button', { name: '开始体验' }));
    expect(push).toHaveBeenLastCalledWith('/lab/community');
  });

  test('点「我要反馈」打开反馈弹窗（不跳转，不弹 toast 占位）', () => {
    render(<LabLandingPage />);
    fireEvent.click(screen.getByRole('button', { name: /我要反馈/ }));
    // 反馈弹窗渲染标题/提交按钮（FeedbackModal 走 /api/v1/feedback §5.2.1）。Toast 占位已下线。
    expect(screen.getByRole('button', { name: '提交反馈' })).toBeInTheDocument();
    expect(screen.getByText('反馈关于实验性功能（社区等）的体验与建议，我们会持续优化。')).toBeInTheDocument();
    expect(toastInfo).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});
