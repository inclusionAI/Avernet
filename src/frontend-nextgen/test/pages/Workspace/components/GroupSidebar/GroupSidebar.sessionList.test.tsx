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

  it('会话区树形导轨（验收微调）：干线由会话行自带，容器不再渲染贯穿线', () => {
    render(<GroupSidebar {...makeProps()} />);
    const sessions = screen.getByLabelText('协作群会话列表：主站群');
    expect(sessions).toHaveClass('pl-6');
    // 容器不再渲染贯穿干线（before:* 已移除），干线与末行截断由每行 SessionCard 自带。
    expect(sessions).not.toHaveClass('before:absolute');
    const rails = sessions.querySelectorAll('[data-session-tree-rail]');
    expect(rails.length).toBeGreaterThan(0);
    expect(rails[0]).toHaveClass('-left-2', 'top-0', 'bottom-0', 'w-px', 'bg-border');
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

  it('协作群会话与 Bot 会话统一使用消息 Icon', () => {
    const { container } = render(<GroupSidebar {...makeProps()} />);

    expect(container.querySelectorAll('svg.lucide-message-square')).toHaveLength(2);
    expect(container.querySelector('[data-session-indicator].rounded-full')).not.toBeInTheDocument();
  });

  it('群会话没有次行内容时使用紧凑行高', () => {
    render(<GroupSidebar {...makeProps()} />);

    const sessionTrigger = screen.getByRole('button', { name: /会话一/ });
    expect(sessionTrigger).toHaveClass('min-h-12', 'py-2');
    expect(sessionTrigger.parentElement).toHaveClass('min-h-12');
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
