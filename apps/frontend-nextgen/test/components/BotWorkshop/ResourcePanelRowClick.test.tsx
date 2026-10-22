/** @jest-environment jsdom */

import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import type { BotEditorResource } from '@/domain/botEditor';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const resources: BotEditorResource[] = [
  { path: 'docs', parentPath: '', name: 'docs', type: 'folder' },
  { path: 'docs/guide', parentPath: 'docs', name: 'guide', type: 'folder' },
  { path: 'README.md', parentPath: '', name: 'README.md', type: 'file', size: 2048 },
];

function renderPanel(overrides: Partial<Parameters<typeof ResourcePanel>[0]> = {}) {
  const props = {
    resources,
    editable: true,
    onCreateDirectory: jest.fn().mockResolvedValue(undefined),
    onDelete: jest.fn().mockResolvedValue(undefined),
    onUpload: jest.fn().mockResolvedValue(undefined),
    onPreview: jest.fn().mockResolvedValue({ kind: 'text' as const, content: '', contentType: 'text/plain' }),
    onDownload: jest.fn().mockResolvedValue(undefined),
    onCopyPath: jest.fn().mockResolvedValue(undefined),
    onLoadDirectory: jest.fn().mockResolvedValue(undefined),
    loadingPaths: [],
    ...overrides,
  };
  render(<ResourcePanel {...props} />);
  return props;
}

/** 目录行名称与路径同文本，但面包屑也会渲染同名段；改按行热区按钮的 aria-label 精确命中「展开/收起docs」。 */
function clickDocsName() {
  fireEvent.click(screen.getByRole('button', { name: /^(展开|收起)docs$/ }));
}

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('点击目录行名称区域即展开并懒加载子目录', async () => {
  const { onLoadDirectory } = renderPanel();

  expect(screen.queryByText('guide')).not.toBeInTheDocument();

  clickDocsName();

  expect(onLoadDirectory).toHaveBeenCalledWith('docs');
  expect(await screen.findByText('guide')).toBeInTheDocument();
});

test('深层级行按祖先层级渲染缩进导轨', async () => {
  renderPanel();
  clickDocsName();
  await screen.findByText('guide');

  // guide 为 depth-1 目录，其子文件行（不存在则以 guide 自身断言）：guide 行应有 1 根导轨
  const guideRow = screen.getByText('guide').closest('div[data-depth]');
  expect(guideRow).toHaveAttribute('data-depth', '1');
  expect(guideRow?.querySelectorAll('span[aria-hidden][data-rail]').length).toBeGreaterThan(0);
});

test('再次点击名称区域收起目录，且已加载目录不重复请求', async () => {
  const { onLoadDirectory } = renderPanel();

  clickDocsName();
  await screen.findByText('guide');

  clickDocsName();
  expect(screen.queryByText('guide')).not.toBeInTheDocument();

  clickDocsName();
  expect(await screen.findByText('guide')).toBeInTheDocument();
  expect(onLoadDirectory).toHaveBeenCalledTimes(1);
});

test('点击下载不触发展开（操作区与热区互为兄弟节点）', () => {
  const { onLoadDirectory, onDownload } = renderPanel();

  fireEvent.click(screen.getByRole('button', { name: '下载文件夹docs' }));

  expect(onDownload).toHaveBeenCalledWith('docs', 'folder');
  expect(onLoadDirectory).not.toHaveBeenCalled();
  expect(screen.queryByText('guide')).not.toBeInTheDocument();
});

test('目录节点支持向当前目录添加文件并刷新该目录', async () => {
  const { onUpload, onLoadDirectory } = renderPanel();
  const file = new File(['hello'], 'hello.txt', { type: 'text/plain' });

  fireEvent.click(screen.getByRole('button', { name: '向docs添加文件' }));
  fireEvent.change(screen.getByLabelText('资源文件选择'), { target: { files: [file] } });

  expect(onUpload).toHaveBeenCalledWith('docs/hello.txt', file);
  await waitFor(() => expect(onLoadDirectory).toHaveBeenCalledWith('docs'));
});

test('目录节点支持在当前目录新建子文件夹并刷新该目录', async () => {
  const { onCreateDirectory, onLoadDirectory } = renderPanel();

  fireEvent.click(screen.getByRole('button', { name: '在docs中新建子目录' }));
  fireEvent.change(screen.getByPlaceholderText('输入目录名称'), { target: { value: 'assets' } });
  fireEvent.click(screen.getByRole('button', { name: '创建' }));

  expect(onCreateDirectory).toHaveBeenCalledWith('docs/assets');
  await waitFor(() => expect(onLoadDirectory).toHaveBeenCalledWith('docs'));
});

test('文件和目录节点都支持复制路径', () => {
  const { onCopyPath } = renderPanel();

  fireEvent.click(screen.getByRole('button', { name: '复制路径docs' }));
  fireEvent.click(screen.getByRole('button', { name: '复制路径README.md' }));

  expect(onCopyPath).toHaveBeenNthCalledWith(1, 'docs');
  expect(onCopyPath).toHaveBeenNthCalledWith(2, 'README.md');
});

test('点击文件名称从右侧抽屉打开预览', async () => {
  const onPreview = jest.fn().mockResolvedValue({ kind: 'text', content: '# TeamClaw', contentType: 'text/markdown' });
  renderPanel({ onPreview });

  fireEvent.click(screen.getByRole('button', { name: '预览README.md' }));

  expect(onPreview).toHaveBeenCalledWith('README.md');
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(screen.getByText('# TeamClaw')).toBeInTheDocument();
});

test('预览失败（如大文件超限）不打开抽屉且 rejection 不逃逸', async () => {
  // toast 由全局错误通道负责；组件侧吞掉传播即可，否则 dev 下弹 Unhandled Rejection 遮罩
  const onPreview = jest.fn().mockRejectedValue(new Error('File too large for preview'));
  renderPanel({ onPreview });

  fireEvent.click(screen.getByRole('button', { name: '预览README.md' }));

  await waitFor(() => expect(onPreview).toHaveBeenCalled());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('删除确认弹层内取消不会误折叠已展开目录', async () => {
  const { onLoadDirectory } = renderPanel();

  clickDocsName();
  await screen.findByText('guide');

  fireEvent.click(screen.getByRole('button', { name: '删除docs' }));
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));

  // Radix 弹层经 Portal 渲染，但合成事件沿 React 组件树冒泡；
  // 热区与操作区是兄弟节点，弹层内点击不应冒泡到热区触发折叠。
  expect(await screen.findByText('guide')).toBeInTheDocument();
  expect(onLoadDirectory).toHaveBeenCalledTimes(1);
});
