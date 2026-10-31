/** @jest-environment jsdom */
// 实验室落地页:功能卡网格(社区 + 任务)。PRD(TeamClaw-demo /lab):任务卡点「开始体验」
// 进入任务列表页(/work/my-task,状态类别筛选 + V2 任务副屏抽屉)。
import LabLandingPage from '@/pages/Lab';
import { history } from '@umijs/max';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from '@jest/globals';

// Lab 页静态 import @umijs/max history;真模块进 jest 依赖图会踩 esbuild Buffer 不变量,必须 mock。
// factory 经 hoist 先于 @jest/globals 绑定求值,故用全局 jest(同 defaultCapabilities.test.ts 先例)。
jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

const pushMock = history.push as unknown as jest.Mock;

describe('LabLandingPage(实验室落地页)', () => {
  beforeEach(() => {
    pushMock.mockClear();
  });

  it('渲染社区与任务两张功能卡，并为描述保留两行高度', () => {
    render(<LabLandingPage />);
    expect(screen.getByRole('article', { name: '社区' })).toBeTruthy();
    expect(screen.getByRole('article', { name: '任务' })).toBeTruthy();
    for (const card of [
      screen.getByRole('article', { name: '社区' }),
      screen.getByRole('article', { name: '任务' }),
    ]) {
      const descriptions = within(card).getAllByText(/./, { selector: 'p' });
      expect(descriptions).toHaveLength(1);
      expect(descriptions[0].className).toContain('min-h-12');
    }
  });

  it('任务卡:描述对齐 PRD 文案;开始体验跳任务列表页(任务类别)', () => {
    render(<LabLandingPage />);
    const taskCard = screen.getByRole('article', { name: '任务' });
    const card = within(taskCard);
    expect(card.getByText(/查看跨Bot、跨群的协同任务/)).toBeTruthy();
    expect(card.getByText(/可在对话或协作群中发起/)).toBeTruthy();

    fireEvent.click(card.getByRole('button', { name: /开始体验/ }));
    expect(pushMock).toHaveBeenCalledWith('/lab/tasks');
  });

  it('社区卡:开始体验仍跳 /lab/community(回归)', () => {
    render(<LabLandingPage />);
    const community = within(screen.getByRole('article', { name: '社区' }));
    fireEvent.click(community.getByRole('button', { name: /开始体验/ }));
    expect(pushMock).toHaveBeenCalledWith('/lab/community');
  });
});