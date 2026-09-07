/** @jest-environment jsdom */
import {
  ResizableWorkspaceSidebar,
  WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY,
  WORKSPACE_SIDEBAR_COLLAPSED_WIDTH,
  WORKSPACE_SIDEBAR_DEFAULT_WIDTH,
  WORKSPACE_SIDEBAR_MAX_WIDTH,
  WORKSPACE_SIDEBAR_MIN_WIDTH,
  WORKSPACE_SIDEBAR_STORAGE_KEY,
} from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

let containerWidth = 1200;

function rect(width: number): DOMRect {
  return {
    width,
    height: 600,
    top: 0,
    right: width,
    bottom: 600,
    left: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  };
}

// jsdom 无 PointerEvent 构造函数，RTL 的 fireEvent.pointerDown 会回退为普通 Event，
// 丢失 button/clientX，导致组件的左键守卫与拖动数学失效。这里用预构造的 MouseEvent 交给
// fireEvent 派发：既保留 button/clientX，又经由 act 同步提交状态（确保拖拽监听在移动前挂载）。
// 真实浏览器中 PointerEvent 同样携带这些属性，组件行为一致。
function dispatchPointer(
  target: Element | Document,
  type: string,
  init: { button?: number; clientX?: number; bubbles?: boolean },
) {
  fireEvent(
    target,
    new MouseEvent(type, {
      bubbles: init.bubbles ?? true,
      cancelable: true,
      button: init.button ?? 0,
      clientX: init.clientX ?? 0,
    }),
  );
}

function renderSidebar() {
  return render(
    <div>
      <ResizableWorkspaceSidebar ariaLabel="测试会话侧栏" collapsedContent={<button type="button">快捷入口</button>}>
        <div>列表内容</div>
      </ResizableWorkspaceSidebar>
      <main>消息区</main>
    </div>,
  );
}

describe('ResizableWorkspaceSidebar', () => {
  beforeEach(() => {
    window.localStorage.clear();
    containerWidth = 1200;
    jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => rect(containerWidth));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('默认使用 320px，并输出可访问宽度阈值', () => {
    renderSidebar();

    expect(screen.getByLabelText('测试会话侧栏')).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_DEFAULT_WIDTH}px` });
    expect(screen.getByRole('separator', { name: '调整对话协作左栏宽度' })).toHaveAttribute(
      'aria-valuemin',
      String(WORKSPACE_SIDEBAR_MIN_WIDTH),
    );
    expect(screen.getByRole('separator', { name: '调整对话协作左栏宽度' })).toHaveAttribute(
      'aria-valuemax',
      String(WORKSPACE_SIDEBAR_MAX_WIDTH),
    );
  });

  it('拖拽调宽胶囊附近提供一键收起按钮', () => {
    renderSidebar();
    expect(screen.getByTestId('workspace-sidebar-grip')).toHaveClass('h-8', 'w-6', 'rounded-full');
    expect(screen.getByRole('button', { name: '收起对话协作左栏' })).toHaveClass(
      'right-0',
      'top-1/2',
      '-mt-10',
      'h-8',
      'w-6',
      'rounded-full',
    );
    expect(screen.getByRole('button', { name: '收起对话协作左栏' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByRole('button', { name: '展开对话协作左栏' })).not.toBeInTheDocument();
  });

  it('一键收起后保留快捷图标栏，并提供贴边恢复按钮', () => {
    renderSidebar();
    const sidebar = screen.getByLabelText('测试会话侧栏');

    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));

    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_COLLAPSED_WIDTH}px` });
    expect(sidebar).toHaveAttribute('data-collapsed', 'true');
    expect(screen.getByText('列表内容').parentElement).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('separator', { name: '调整对话协作左栏宽度' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '快捷入口' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开对话协作左栏' })).toHaveClass('h-8', 'w-6', 'rounded-full');
    expect(screen.getByRole('button', { name: '展开对话协作左栏' })).toHaveAttribute('aria-expanded', 'false');
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('true');
  });

  it('展开时恢复收起前宽度，不覆盖用户调宽偏好', () => {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_STORAGE_KEY, '400');
    renderSidebar();
    const sidebar = screen.getByLabelText('测试会话侧栏');

    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));
    fireEvent.click(screen.getByRole('button', { name: '展开对话协作左栏' }));

    expect(sidebar).toHaveStyle({ width: '400px' });
    expect(sidebar).toHaveAttribute('data-collapsed', 'false');
    expect(screen.getByText('列表内容').parentElement).toHaveAttribute('aria-hidden', 'false');
    expect(screen.queryByRole('button', { name: '快捷入口' })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY)).toBe('400');
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('false');
  });

  it('读取已持久化收起状态', () => {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
    renderSidebar();

    expect(screen.getByLabelText('测试会话侧栏')).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_COLLAPSED_WIDTH}px` });
    expect(screen.getByRole('button', { name: '展开对话协作左栏' })).toBeInTheDocument();
  });

  it('拖动时钳制宽度，松开后持久化', () => {
    renderSidebar();
    const sidebar = screen.getByLabelText('测试会话侧栏');
    const separator = screen.getByRole('separator', { name: '调整对话协作左栏宽度' });

    dispatchPointer(separator, 'pointerdown', { button: 0, clientX: 320 });
    dispatchPointer(document, 'pointermove', { clientX: 800 });
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_MAX_WIDTH}px` });
    dispatchPointer(document, 'pointerup', { clientX: 800 });
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY)).toBe(String(WORKSPACE_SIDEBAR_MAX_WIDTH));

    dispatchPointer(separator, 'pointerdown', { button: 0, clientX: 480 });
    dispatchPointer(document, 'pointermove', { clientX: 0 });
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_MIN_WIDTH}px` });
    dispatchPointer(document, 'pointerup', { clientX: 0 });
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY)).toBe(String(WORKSPACE_SIDEBAR_MIN_WIDTH));
  });

  it('仅响应左键，右键不启动拖动', () => {
    renderSidebar();
    const sidebar = screen.getByLabelText('测试会话侧栏');
    const separator = screen.getByRole('separator', { name: '调整对话协作左栏宽度' });

    dispatchPointer(separator, 'pointerdown', { button: 2, clientX: 320 });
    dispatchPointer(document, 'pointermove', { clientX: 800 });
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_DEFAULT_WIDTH}px` });
  });

  it('支持键盘步进、阈值跳转和双击复位', () => {
    renderSidebar();
    const sidebar = screen.getByLabelText('测试会话侧栏');
    const separator = screen.getByRole('separator', { name: '调整对话协作左栏宽度' });

    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(sidebar).toHaveStyle({ width: '328px' });
    fireEvent.keyDown(separator, { key: 'ArrowRight', shiftKey: true });
    expect(sidebar).toHaveStyle({ width: '352px' });
    fireEvent.keyDown(separator, { key: 'Home' });
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_MIN_WIDTH}px` });
    fireEvent.keyDown(separator, { key: 'End' });
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_MAX_WIDTH}px` });
    fireEvent.doubleClick(separator);
    expect(sidebar).toHaveStyle({ width: `${WORKSPACE_SIDEBAR_DEFAULT_WIDTH}px` });
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY)).toBe(String(WORKSPACE_SIDEBAR_DEFAULT_WIDTH));
  });

  it('读取浏览器偏好，并按工作区宽度 45% 动态钳制', () => {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_STORAGE_KEY, '460');
    containerWidth = 800;

    renderSidebar();

    expect(screen.getByLabelText('测试会话侧栏')).toHaveStyle({ width: '360px' });
    expect(screen.getByRole('separator', { name: '调整对话协作左栏宽度' })).toHaveAttribute('aria-valuemax', '360');
    expect(window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY)).toBe('460');
  });
});
