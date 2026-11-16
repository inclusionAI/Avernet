/** @jest-environment jsdom */
// 实验室落地页：社区、待办、任务是三个独立入口；待办为新增能力，任务保持原路由与行为。
import LabLandingPage from '@/pages/Lab';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { history } from '@umijs/max';

// Lab 页静态 import @umijs/max history;真模块进 jest 依赖图会踩 esbuild Buffer 不变量,必须 mock。
// factory 经 hoist 先于 @jest/globals 绑定求值,故用全局 jest(同 defaultCapabilities.test.ts 先例)。
jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

const pushMock = history.push as unknown as jest.Mock;

describe('LabLandingPage(实验室落地页)', () => {
  beforeEach(() => {
    pushMock.mockClear();
  });

  it('按原型顺序渲染社区、待办、任务三张功能卡，并为描述保留两行高度', () => {
    render(<LabLandingPage />);
    const cards = screen.getAllByRole('article');
    expect(cards).toHaveLength(3);
    expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual(['社区', '待办', '任务']);
    for (const card of cards) {
      const descriptions = within(card).getAllByText(/./, { selector: 'p' });
      expect(descriptions).toHaveLength(1);
      expect(descriptions[0].className).toContain('min-h-12');
    }
  });

  it('待办卡：使用独立文案并进入新增待办页', () => {
    render(<LabLandingPage />);
    const todoCard = within(screen.getByRole('article', { name: '待办' }));
    expect(todoCard.getByText(/让 Bot 服务团队、帮助完成团队工作/)).toBeTruthy();
    expect(todoCard.queryByText(/团队知识库/)).toBeNull();

    fireEvent.click(todoCard.getByRole('button', { name: /开始体验/ }));
    expect(pushMock).toHaveBeenCalledWith('/lab/todos');
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
