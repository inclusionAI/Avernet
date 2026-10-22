/** @jest-environment jsdom */
// 功能域：群会话列表加载/错误重试、分页加载更多、展开渲染与空态。
// 从原 GroupSidebar.test.tsx 拆出（断言与渲染逻辑零改动），用于并行模式下多 worker 分摊。
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

import { baseGroup, makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

beforeEach(setupResizeObserverMock);

describe('GroupSidebar', () => {
  it('群会话错误保留父对象并提供局部重试', () => {
    const onReloadSession = jest.fn().mockResolvedValue(undefined);
    render(
      <GroupSidebar
        {...makeProps({
          sessionsByGroupId: {},
          errorByGroupId: { g1: '群会话加载失败' },
          onReloadSession,
        })}
      />,
    );
    expect(screen.getByText('主站群')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('群会话加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onReloadSession).toHaveBeenCalledWith('g1');
  });

  it('nested sessions render under selected group, clicking session fires onSelectSession', () => {
    const onSelectSession = jest.fn();
    render(<GroupSidebar {...makeProps({ onSelectSession })} />);
    fireEvent.click(screen.getByText('会话一'));
    expect(onSelectSession).toHaveBeenCalledWith('g1', 's1');
  });

  it('群会话使用更多操作菜单承载会话管理，操作不触发会话选择', () => {
    const onManageSession = jest.fn();
    const onSelectSession = jest.fn();
    const { container } = render(<GroupSidebar {...makeProps({ onManageSession, onSelectSession })} />);

    const moreButtons = screen.getAllByRole('button', { name: '会话更多操作' });
    expect(container.querySelector('.self-start')).not.toBeInTheDocument();
    expect(moreButtons).toHaveLength(2);
    fireEvent.click(moreButtons[0]);
    fireEvent.click(screen.getByRole('button', { name: '管理会话' }));

    expect(onManageSession).toHaveBeenCalledWith('g1', 's1');
    expect(onSelectSession).not.toHaveBeenCalled();
  });

  it('does not render session member count', () => {
    const sessions = [
      {
        ...baseGroup.sessions[0],
        participants: [],
        participantCount: 3,
      },
    ];
    render(<GroupSidebar {...makeProps({ sessionsByGroupId: { g1: sessions } })} />);
    expect(screen.queryByText(/3 个成员/)).not.toBeInTheDocument();
  });

  it('会话区：容器无缩进导轨/贯穿线，行标题对齐固定头像槽后的一级名称', () => {
    render(<GroupSidebar {...makeProps()} />);
    const sessions = screen.getByLabelText('协作群会话列表：主站群');
    expect(sessions).not.toHaveClass('pl-6');
    expect(sessions.querySelectorAll('[data-session-tree-rail]').length).toBe(0);
    expect(sessions.querySelectorAll('[data-session-tree-elbow]').length).toBe(0);
    // 会话行由 52px margin + 8px padding 对齐一级名称，胶囊 hover/选中同几何。
    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveClass('ml-[52px]');
    const capsule = sessionTrigger.previousElementSibling as HTMLElement;
    expect(capsule).toHaveClass('left-2', 'right-2', 'rounded-md');
  });

  it('选中会话胶囊升级品牌蓝 bg-primary/10，标题点亮 text-primary（2026-10-22 对话侧同轮裁决）', () => {
    const { rerender } = render(<GroupSidebar {...makeProps()} />);
    rerender(<GroupSidebar {...makeProps({ selectedSessionId: 's1' })} />);
    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveAttribute('aria-current', 'page');
    const capsule = sessionTrigger.previousElementSibling as HTMLElement;
    // 几何契约不变（inset-x-2 全宽 rx6）；壳层会话选中语言已统一定稿为品牌蓝。
    expect(capsule).toHaveClass('bg-primary/10');
    expect(capsule).not.toHaveClass('bg-selected');
    expect(screen.getByText('会话一')).toHaveClass('text-primary');
  });

  it('选中协作群暂无会话时展示明确空态', () => {
    render(
      <GroupSidebar
        {...makeProps({
          selectedGroupId: 'g1',
          sessionsByGroupId: { g1: [] },
        })}
      />,
    );

    expect(screen.getByText('当前协作群暂无会话')).toBeInTheDocument();
    // 空态升级为引导卡（2026-10-22）：陈述之外给「点哪」的下一步动作。
    expect(screen.getByText('点击群行尾的「＋」按钮，发起第一场协作会话')).toBeInTheDocument();
    expect(screen.queryByText('暂无协作群临时会话')).not.toBeInTheDocument();
  });

  it('group collapse toggle hides sessions', () => {
    render(<GroupSidebar {...makeProps({ expandedGroupIds: {} })} />);
    expect(screen.queryByText('会话一')).not.toBeInTheDocument();
  });

  it('展开会话区不再渲染旧会话范围工具栏', () => {
    render(<GroupSidebar {...makeProps()} />);

    expect(screen.queryByRole('group', { name: '会话范围筛选' })).not.toBeInTheDocument();
  });

  it('v2 会话行无前置消息图标、无日期列；相对时间走右缘轻提示（2026-10-22 精修）', () => {
    const { container } = render(<GroupSidebar {...makeProps()} />);

    expect(container.querySelectorAll('svg.lucide-message-square')).toHaveLength(0);
    expect(container.querySelector('[data-session-indicator]')).not.toBeInTheDocument();
  });

  it('v2 群会话使用 32px 紧凑行高（h-8，对齐对话会话行几何）', () => {
    render(<GroupSidebar {...makeProps()} />);

    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveClass('h-8');
    expect(sessionTrigger.parentElement).toHaveClass('min-h-8');
    expect(sessionTrigger.textContent).not.toContain('个成员');
  });

  it('loads more sessions without toggling the group card', () => {
    const onLoadMoreSessions = jest.fn<(groupId: string) => Promise<void>>().mockResolvedValue(undefined);

    const onToggleGroupExpanded = jest.fn();
    render(
      <GroupSidebar
        {...makeProps({
          onLoadMoreSessions,
          onToggleGroupExpanded,
          hasMoreSessionsByGroupId: { g1: true },
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '加载更多会话' }));
    expect(onLoadMoreSessions).toHaveBeenCalledWith('g1');
    expect(onToggleGroupExpanded).not.toHaveBeenCalled();
  });
});
