/** @jest-environment jsdom */
// Preview Drawer 的加载状态、富媒体、关闭与下载分支。
import { type TaskArtifactPreviewState, useTaskArtifactPreview } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { TaskArtifactPreviewDrawer } from '@/assets/TaskPanel/v2/TaskArtifactPreviewDrawer';
import { beforeEach, describe, expect, it } from '@jest/globals';
import type { jest as jestType } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
  useTaskArtifactPreview: jest.fn(),
}));

const mockedUsePreview = useTaskArtifactPreview as jestType.MockedFunction<typeof useTaskArtifactPreview>;
const artifactHooks =
  require('@/assets/TaskPanel/hooks/useTaskArtifactPreview') as typeof import('@/assets/TaskPanel/hooks/useTaskArtifactPreview');
const mockedDownload = artifactHooks.downloadTaskArtifact as jestType.MockedFunction<typeof artifactHooks.downloadTaskArtifact>;

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

const preview = (state: Partial<TaskArtifactPreviewState>): TaskArtifactPreviewState => ({
  kind: 'other',
  status: 'ready',
  contentUrl: null,
  text: null,
  truncated: false,
  size: null,
  errorMessage: null,
  ...state,
});

const fileArtifact = (id: string, overrides: Partial<TaskArtifactView> = {}): TaskArtifactView => ({
  id,
  name: `${id}.pdf`,
  type: 'file',
  kind: 'file',
  owner: 'bot-1',
  updatedAt: '2026-10-20T10:00:00.000Z',
  fileRef: {
    storageType: 'session_file',
    storageId: 'session-1',
    fileId: 'file-1',
    fileType: 'application/pdf',
  },
  ...overrides,
});

const renderDrawer = (artifact: TaskArtifactView, onClose = jest.fn(), portalContainer?: HTMLElement) =>
  render(<TaskArtifactPreviewDrawer artifact={artifact} context={context} onClose={onClose} portalContainer={portalContainer} />);

beforeEach(() => {
  mockedUsePreview.mockReset();
  mockedDownload.mockReset();
  mockedUsePreview.mockReturnValue(preview({ status: 'loading' }));
});

describe('TaskArtifactPreviewDrawer 预览状态', () => {
  it('loading 状态展示加载提示', () => {
    renderDrawer(fileArtifact('loading'));
    expect(screen.getByRole('status')).toHaveTextContent('正在加载产物预览…');
  });

  it('error 状态优先展示后端错误', () => {
    mockedUsePreview.mockReturnValue(preview({ status: 'error', errorMessage: '资源不存在' }));
    renderDrawer(fileArtifact('error'));
    expect(screen.getByRole('alert')).toHaveTextContent('资源不存在');
  });

  it('error 且无消息时使用兜底文案', () => {
    mockedUsePreview.mockReturnValue(preview({ status: 'error' }));
    renderDrawer(fileArtifact('error-default'));
    expect(screen.getByRole('alert')).toHaveTextContent('预览加载失败，请稍后重试。');
  });

  it('unsupported 状态展示提示下载', () => {
    mockedUsePreview.mockReturnValue(preview({ status: 'unsupported' }));
    renderDrawer(fileArtifact('unsupported'));
    expect(screen.getByRole('status')).toHaveTextContent('该文件类型暂不支持站内预览，请下载后查看。');
  });

  it('image/pdf/html/video 分别使用对应预览器', () => {
    let cleanupRender = render(<span />);
    mockedUsePreview.mockReturnValue(preview({ kind: 'image', contentUrl: '/image.png', size: 1024 }));
    cleanupRender = renderDrawer(fileArtifact('image'));
    expect(screen.getByRole('img', { name: 'image.pdf' })).toHaveAttribute('src', '/image.png');

    cleanupRender.unmount();
    mockedUsePreview.mockReturnValue(preview({ kind: 'video', contentUrl: '/video.mp4' }));
    renderDrawer(fileArtifact('video'));
    const video = screen.getByTestId('task-artifact-preview-drawer').querySelector('video');
    expect(video).toHaveAttribute('src', '/video.mp4');
  });

  it('html 预览启用空沙箱，pdf 不设置沙箱', () => {
    mockedUsePreview.mockReturnValue(preview({ kind: 'html', contentUrl: '/preview.html' }));
    const htmlCleanup = renderDrawer(fileArtifact('html'));
    expect(screen.getByTitle('html.pdf 预览')).toHaveAttribute('sandbox', '');
    htmlCleanup.unmount();

    mockedUsePreview.mockReturnValue(preview({ kind: 'pdf', contentUrl: '/preview.pdf' }));
    const pdfCleanup = renderDrawer(fileArtifact('pdf'));
    expect(screen.getByTitle('pdf.pdf 预览')).not.toHaveAttribute('sandbox');
    pdfCleanup.unmount();
  });

  it('其他 ready 状态展示无可展示的提示', () => {
    mockedUsePreview.mockReturnValue(preview({ kind: 'other' }));
    renderDrawer(fileArtifact('other'));
    expect(screen.getByRole('status')).toHaveTextContent('暂无可展示的预览内容，请下载后查看。')
  });
});

describe('TaskArtifactPreviewDrawer 关闭与容器', () => {
  it('ESC、 backdrop 和关闭按钮都会关闭预览', () => {
    const onClose = jest.fn();
    renderDrawer(fileArtifact('close'), onClose);

    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.mouseDown(screen.getByTestId('task-artifact-preview-drawer'));
    fireEvent.mouseDown(screen.getByRole('dialog'));
    fireEvent.click(screen.getByRole('button', { name: '关闭产物预览' }));
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));

    expect(onClose).toHaveBeenCalledTimes(4);
  });

  it('portalContainer 存在时使用宿主内布局', () => {
    const container = document.createElement('div');
    render(
      <TaskArtifactPreviewDrawer
        artifact={fileArtifact('contained')}
        context={context}
        onClose={jest.fn()}
        portalContainer={container}
      />,
    );
    expect(container.querySelector('[data-testid="task-artifact-preview-drawer"]')).toHaveStyle({ position: 'absolute' });
    expect(within(container).getByRole('dialog')).toHaveStyle({ width: '100%' });
  });
});

describe('TaskArtifactPreviewDrawer 下载', () => {
  it('下载失败时展示可读错误，非 Error 使用兜底消息', async () => {
    mockedUsePreview.mockReturnValue(preview({ kind: 'pdf', contentUrl: '/preview.pdf', size: 2048 }));
    mockedDownload.mockRejectedValueOnce(new Error('下载超时')).mockRejectedValueOnce('下载失败');
    const { rerender } = renderDrawer(fileArtifact('download-error'));

    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('下载超时');
    expect(document.getElementById('task-artifact-preview-description')).toHaveTextContent(
      'session-1 · session_file · 2.0 KB',
    );

    rerender(<TaskArtifactPreviewDrawer artifact={fileArtifact('download-error')} context={context} onClose={jest.fn()} />);
    mockedUsePreview.mockReturnValue(preview({ kind: 'pdf', contentUrl: '/preview.pdf' }));
    fireEvent.click(screen.getByRole('button', { name: /下载文件|下载中…/ }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('产物下载失败，请稍后重试。'));
  });

  it('下载中禁用按钮并复用当前状态', async () => {
    mockedUsePreview.mockReturnValue(preview({ kind: 'pdf', contentUrl: '/preview.pdf' }));
    let release!: () => void;
    mockedDownload.mockReturnValue(
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );

    renderDrawer(fileArtifact('downloading'));
    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    const button = await screen.findByRole('button', { name: '下载中…' });
    expect(button).toBeDisabled();
    expect(mockedDownload).toHaveBeenCalledWith(fileArtifact('downloading'), context);

    release();
    await waitFor(() => expect(screen.getByRole('button', { name: '下载文件' })).not.toBeDisabled());
  });
});

describe('TaskArtifactPreviewDrawer 详情回退', () => {
  it('无链接信息时使用未提供文案', () => {
    renderDrawer({
      id: 'link-missing',
      name: '链接产物',
      type: 'link',
      kind: null,
      url: null,
      updatedAt: '',
      owner: null,
      linkRef: { url: null, type: null },
    });
    const details = screen.getByTestId('task-artifact-link-details');
    expect(screen.queryByRole('link', { name: '打开链接' })).not.toBeInTheDocument();
    expect(within(details).getAllByText('未提供')).toHaveLength(4);
  });

  it('代码产物可在 PR 地址缺失时打开代码库地址', () => {
    renderDrawer({
      id: 'code-missing',
      name: '代码产物',
      type: 'other',
      kind: 'code',
      url: null,
      summary: null,
      updatedAt: '',
      ownerLabel: null,
      codeRef: {
        codeBaseName: null,
        codeBaseAddress: 'https://git.example/repo',
        pullRequestUrl: null,
        description: null,
      },
    });
    expect(screen.getByRole('link', { name: '查看 PR' })).toHaveAttribute('href', 'https://git.example/repo');
    const details = screen.getByTestId('task-artifact-code-details');
    expect(within(details).getAllByText('未提供')).toHaveLength(4);
    expect(within(details).getByText('暂无描述')).toBeInTheDocument();
  });

  it('代码产物没有任何地址时不渲染外部跳转按钮', () => {
    renderDrawer({
      id: 'code-empty',
      name: '空代码产物',
      type: 'other',
      kind: 'code',
      url: null,
      codeRef: null,
      updatedAt: '',
    });
    expect(screen.queryByRole('link', { name: '查看 PR' })).not.toBeInTheDocument();
    expect(screen.getByTestId('task-artifact-code-details')).toBeInTheDocument();
  });

  it('普通 Key 不触发关闭，且 activeElement 缺失仍可渲染', () => {
    const prototype = Object.getPrototypeOf(document) as typeof Document.prototype;
    const original = Object.getOwnPropertyDescriptor(prototype, 'activeElement');
    Object.defineProperty(document, 'activeElement', {
      configurable: true,
      get: () => null,
    });

    const onClose = jest.fn();
    renderDrawer(fileArtifact('non-escape'), onClose);
    fireEvent.keyDown(document, { key: 'A' });
    expect(onClose).not.toHaveBeenCalled();

    if (original) {
      Object.defineProperty(document, 'activeElement', original);
    }
  });
});
