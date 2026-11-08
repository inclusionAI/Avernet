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

  test('渲染实验室落地页：hero 标题 + 社区/任务两张功能卡', () => {
    render(<LabLandingPage />);
    expect(screen.getByRole('heading', { name: '实验室' })).toBeInTheDocument();
    // PRD(TeamClaw-demo /lab)：社区 + 任务 两卡。
    const cards = screen.getAllByRole('article');
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByRole('heading', { name: '社区' })).toBeInTheDocument();
    expect(within(cards[1]).getByRole('heading', { name: '任务' })).toBeInTheDocument();
    expect(within(cards[1]).getByText(/查看跨Bot、跨群的协同任务/)).toBeInTheDocument();

    const card = within(cards[0]);
    expect(card.getByRole('heading', { name: '社区' })).toHaveClass('text-base');
    // 齿轮「Bot 访问社区配置」位于社区卡右上角（进入社区前可见）。
    expect(card.getByRole('button', { name: '设置 Bot 访问社区' })).toBeInTheDocument();
    const startButton = card.getByRole('button', { name: '开始体验' });
    expect(startButton).toBeInTheDocument();
    expect(startButton).toHaveClass('bg-primary', 'text-primary-foreground');
    expect(startButton).not.toHaveClass('bg-foreground');
    const feedbackButton = card.getByRole('button', { name: /我要反馈/ });
    const actions = feedbackButton.parentElement;
    expect(actions?.className).toContain('justify-between');
    expect(actions?.lastElementChild).toBe(feedbackButton);
    expect(feedbackButton.querySelector('svg')).toBeNull();
  });

  test('点社区「开始体验」跳 /lab/community', () => {
    render(<LabLandingPage />);
    const community = within(screen.getByRole('article', { name: '社区' }));
    fireEvent.click(community.getByRole('button', { name: '开始体验' }));
    expect(push).toHaveBeenLastCalledWith('/lab/community');
  });

  test('点任务卡「开始体验」跳任务列表页（任务类别）', () => {
    render(<LabLandingPage />);
    const tasks = within(screen.getByRole('article', { name: '任务' }));
    fireEvent.click(tasks.getByRole('button', { name: '开始体验' }));
    expect(push).toHaveBeenLastCalledWith('/lab/tasks');
  });

  test('点社区卡「我要反馈」打开反馈弹窗（不跳转，不弹 toast 占位）', () => {
    render(<LabLandingPage />);
    const community = within(screen.getByRole('article', { name: '社区' }));
    fireEvent.click(community.getByRole('button', { name: /我要反馈/ }));
    // 反馈弹窗渲染标题/提交按钮（FeedbackModal 走 /openapi/v1/collaboration/tasks/feedback §5.2.1）。Toast 占位已下线。
    expect(screen.getByRole('button', { name: '提交反馈' })).toBeInTheDocument();
    expect(screen.getByText('反馈关于实验性功能（社区等）的体验与建议，我们会持续优化。')).toBeInTheDocument();
    expect(toastInfo).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});
