/** @jest-environment jsdom */
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/pages/Workspace/components/workspaceSidebarSizing';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { baseGroup, makeProps, setupResizeObserverMock } from './GroupSidebar.testUtils';

beforeEach(() => {
  setupResizeObserverMock();
  localStorage.clear();
});

it('定位展开已收起侧栏，渲染并滚动到首屏以外的当前会话，不触发选中或改写分页', async () => {
  localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
  const scroll = jest.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  const target = { ...baseGroup.sessions[0], sessionId: 'off-page', title: '深链会话' };
  const props = makeProps({
    selectedGroupId: 'g1',
    selectedSessionId: target.sessionId,
    expandedGroupIds: { other: true },
    location: { group: baseGroup, session: target, request: 1 },
  });
  const view = render(<GroupSidebar {...props} />);
  const sidebar = screen.getByLabelText('协作群列表');
  await waitFor(() => expect(sidebar).toHaveAttribute('data-collapsed', 'false'));
  const row = screen.getByRole('button', { name: '深链会话' });
  await waitFor(() => expect(scroll).toHaveBeenCalled());
  expect(document.activeElement).toBe(row);
  expect(props.onSelectSession).not.toHaveBeenCalled();
  expect(props.onToggleGroupExpanded).not.toHaveBeenCalled();
  expect(props.sessionsByGroupId.g1).toHaveLength(2);
  scroll.mockClear();
  view.rerender(<GroupSidebar {...props} sessionsByGroupId={{ g1: [...baseGroup.sessions] }} />);
  await new Promise((resolve) => { setTimeout(resolve, 30); });
  expect(scroll).not.toHaveBeenCalled();
  view.rerender(<GroupSidebar {...props} location={{ group: baseGroup, session: target, request: 2 }} />);
  await waitFor(() => expect(scroll).toHaveBeenCalled());
  const dismiss = jest.fn();
  view.rerender(<GroupSidebar {...props} onDismissLocation={dismiss} />);
  fireEvent.click(screen.getByText('主站群'));
  expect(dismiss).toHaveBeenCalledTimes(1);
  view.rerender(<GroupSidebar {...props} location={undefined} />);
  expect(screen.queryByText('深链会话')).not.toBeInTheDocument();
  expect(props.onToggleGroupExpanded).not.toHaveBeenCalled();
});

it('定位群被筛选隐藏时仍可显示目标，取消定位后不遗留额外会话', async () => {
  const scroll = jest.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  const props = makeProps({
    groups: [],
    selectedGroupId: 'g1',
    selectedSessionId: 's1',
    location: { group: baseGroup, session: baseGroup.sessions[0], request: 1 },
  });
  const view = render(<GroupSidebar {...props} />);
  expect(screen.getByText('会话一')).toBeInTheDocument();
  await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
  view.rerender(<GroupSidebar {...props} location={undefined} />);
  expect(screen.queryByText('会话一')).not.toBeInTheDocument();
});
