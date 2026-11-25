/** @jest-environment jsdom */
import { WorkspaceOverlayPanel } from '@/pages/Workspace/components/WorkspaceOverlayPanel';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';

it('设置/文件覆盖层逃离隔离上下文，覆盖完整会话与持久副屏，关闭不卸载副屏', () => {
  const onDismiss = jest.fn();
  const unmountPanel = jest.fn();
  function PersistentPanel() {
    useEffect(() => unmountPanel, []);
    return <div data-testid="persistent-panel">状态机副屏</div>;
  }
  const bounds = jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 320,
    top: 48,
    width: 1000,
    height: 700,
    right: 1320,
    bottom: 748,
    x: 320,
    y: 48,
    toJSON() {},
  });
  function Layout({ open }: { open: boolean }) {
    return (
      <main className="relative">
        <div data-workspace-conversation className="relative isolate">
          <div className="isolate">
            {open && (
              <WorkspaceOverlayPanel ariaLabel="会话管理面板" onDismiss={onDismiss}>
                <button type="button">面板操作</button>
              </WorkspaceOverlayPanel>
            )}
          </div>
        </div>
        <PersistentPanel />
      </main>
    );
  }
  const view = render(<Layout open />);
  const overlay = document.querySelector('[data-workspace-overlay]');
  expect(overlay?.parentElement).toBe(document.body);
  expect(overlay).toHaveClass('fixed', 'z-40');
  expect(overlay).toHaveStyle({ left: '320px', top: '48px', width: '1000px', height: '700px' });
  expect(screen.getByRole('button', { name: '关闭会话管理面板' })).toHaveClass('backdrop-blur-sm');
  fireEvent.pointerDown(screen.getByRole('button', { name: '面板操作' }));
  expect(onDismiss).not.toHaveBeenCalled();

  bounds.mockReturnValue({ left: 280, top: 48, width: 1040, height: 700 } as DOMRect);
  act(() => window.dispatchEvent(new Event('resize')));
  expect(overlay).toHaveStyle({ left: '280px', width: '1040px' });
  const persistent = screen.getByTestId('persistent-panel');
  view.rerender(<Layout open={false} />);
  expect(document.querySelector('[data-workspace-overlay]')).toBeNull();
  expect(screen.getByTestId('persistent-panel')).toBe(persistent);
  expect(unmountPanel).not.toHaveBeenCalled();
  bounds.mockRestore();
});
