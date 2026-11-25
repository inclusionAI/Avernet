/** @jest-environment jsdom */
import { GroupWorkspaceSidebar } from '@/pages/Workspace/components/GroupWorkspaceSidebar';
import { useGroupSessionLocation } from '@/pages/Workspace/hooks/useGroupSessionLocation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { baseGroup, makeProps, setupResizeObserverMock } from './GroupSidebar/GroupSidebar.testUtils';

it.each([true, false])('移动抽屉受控关闭/重开不重放旧定位（先收起群=%s）', async (collapseFirst) => {
  setupResizeObserverMock();
  localStorage.clear();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId: 'human-1', selectedGroupId: 'g1', selectedSessionId: 's1' });
  const scroll = jest.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = jest.fn().mockReturnValue({ matches: true });
  function Harness({ visible = true }: { visible?: boolean }) {
    const [open, setOpen] = useState(false);
    const { locate, location, dismissLocation } = useGroupSessionLocation(baseGroup, baseGroup.sessions[0], () =>
      setOpen(true),
    );
    return (
      <>
        <button type="button" onClick={locate}>
          定位会话
        </button>
        <GroupWorkspaceSidebar
          sidebar={makeProps({
            selectedGroupId: 'g1',
            selectedSessionId: 's1',
            expandedGroupIds: {},
            location,
            onDismissLocation: dismissLocation,
          })}
          open={open && visible}
          onClose={() => setOpen(false)}
        />
      </>
    );
  }
  const view = render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: '定位会话' }));
  const dialog = await screen.findByRole('dialog');
  await waitFor(() => expect(scroll).toHaveBeenCalled());
  expect(within(dialog).getByText('会话一')).toBeInTheDocument();
  if (collapseFirst) fireEvent.click(within(dialog).getByText('主站群'));
  expect(within(dialog).queryAllByText('会话一')).toHaveLength(collapseFirst ? 0 : 1);
  // 模拟受控抽屉关闭后重新挂载内容，定位请求仍由同一父组件持有。
  view.rerender(<Harness visible={false} />);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  scroll.mockClear();
  view.rerender(<Harness visible />);
  const reopened = await screen.findByRole('dialog');
  expect(within(reopened).queryByText('会话一')).not.toBeInTheDocument();
  expect(scroll).not.toHaveBeenCalled();
  view.unmount();
  window.matchMedia = originalMatchMedia;
  useWorkspaceStore.getState().reset();
});

it('移动侧栏打开管理面板时同步关闭列表抽屉，避免抽屉遮挡设置覆盖层', () => {
  setupResizeObserverMock();
  const onClose = jest.fn();
  const dismissLocation = jest.fn();
  const sidebar = makeProps({ onDismissLocation: dismissLocation });
  render(<GroupWorkspaceSidebar sidebar={sidebar} open onClose={onClose} />);
  const dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getAllByRole('button', { name: '会话更多操作' })[0]);
  fireEvent.click(screen.getByRole('button', { name: '管理会话' }));
  expect(sidebar.onManageSession).toHaveBeenCalledWith('g1', 's1');
  expect(dismissLocation).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);
});
