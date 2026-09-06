/** @jest-environment jsdom */

import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import type { BotEditorResource } from '@/domain/botEditor';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

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
    onLoadDirectory: jest.fn().mockResolvedValue(undefined),
    loadingPaths: [],
    ...overrides,
  };
  render(<ResourcePanel {...props} />);
  return props;
}

/** 目录行名称与路径同文本（根目录文件夹 path === name），取第一个即名称行。 */
function clickDocsName() {
  fireEvent.click(screen.getAllByText('docs')[0]);
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
