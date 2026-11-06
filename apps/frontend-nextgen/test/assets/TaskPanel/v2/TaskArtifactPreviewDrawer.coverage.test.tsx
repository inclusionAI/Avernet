/** @jest-environment jsdom */
// TaskArtifactPreviewDrawer 覆盖率补齐:补足预览形态(图片/视频/iframe/错误/兜底)、
// Escape 关闭、下载流(成功/失败/下载中)、无地址链接、portal 宿主细节与无 document 环境分支。
import {
  downloadTaskArtifact,
  type TaskArtifactPreviewState,
  useTaskArtifactPreview,
} from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import { TaskArtifactPreviewDrawer } from '@/assets/TaskPanel/v2/TaskArtifactPreviewDrawer';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { TextEncoder as NodeTextEncoder } from 'node:util';
// jest.mock 工厂在 import 绑定初始化前执行,工厂内使用注入的全局 jest。
import { beforeEach, describe, expect, it } from '@jest/globals';

// jsdom 环境没有 TextEncoder,react-dom/server 在模块加载期就依赖它;
// 先补齐再懒加载 SSR 渲染器(仅无 document 分支的断言用到)。
const globalWithTextEncoder = globalThis as unknown as { TextEncoder?: unknown };
if (typeof globalWithTextEncoder.TextEncoder === 'undefined') {
  globalWithTextEncoder.TextEncoder = NodeTextEncoder;
}
// 历史计划里无 document 分支要拿 renderToString 懒加载断言,最终证明不可达(见文末说明),
// 只保留模块加载这一副作用,去掉未使用的 renderToString 绑定。
jest.requireActual('react-dom/server');

jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
  useTaskArtifactPreview: jest.fn(),
}));

const mockedPreview = useTaskArtifactPreview as jest.MockedFunction<typeof useTaskArtifactPreview>;
const mockedDownload = downloadTaskArtifact as jest.MockedFunction<typeof downloadTaskArtifact>;

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

function fileArtifact(overrides: Record<string, unknown> = {}): TaskArtifactView {
  return {
    id: 'artifact-file',
    name: '调研报告.png',
    type: 'file',
    kind: 'file',
    updatedAt: '',
    fileRef: {
      storageType: 'bot_workspace',
      storageId: 'workspace/调研报告.png',
      fileType: 'image/png',
    },
    ...overrides,
  } as TaskArtifactView;
}

function preview(overrides: Partial<TaskArtifactPreviewState> = {}): TaskArtifactPreviewState {
  return {
    kind: 'other',
    status: 'unsupported',
    contentUrl: null,
    text: null,
    truncated: false,
    size: null,
    errorMessage: null,
    ...overrides,
  };
}

/** 手动释放 Promise,用于「下载中」态的控制。 */
function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** 刷新一轮微任务,让点击后的异步 download 收敛。 */
async function actFlush() {
  await act(async () => {});
}

describe('TaskArtifactPreviewDrawer 覆盖补齐', () => {
  beforeEach(() => {
    mockedPreview.mockReset();
    mockedPreview.mockReturnValue(preview());
    mockedDownload.mockReset();
    mockedDownload.mockResolvedValue(undefined);
  });

  it('图片预览:ready + contentUrl 渲染 <img>', () => {
    mockedPreview.mockReturnValue(preview({ kind: 'image', status: 'ready', contentUrl: 'blob:image-1' }));

    render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={jest.fn()} />);

    const img = screen.getByRole('img', { name: '调研报告.png' });
    expect(img).toHaveAttribute('src', 'blob:image-1');
  });

  it('视频预览:ready + contentUrl 渲染 <video>', () => {
    mockedPreview.mockReturnValue(preview({ kind: 'video', status: 'ready', contentUrl: 'blob:video-1' }));

    render(<TaskArtifactPreviewDrawer artifact={fileArtifact({ name: '演示视频.mp4' })} context={context} onClose={jest.fn()} />);

    const video = document.querySelector('video');
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute('src', 'blob:video-1');
  });

  it('PDF/HTML 预览:ready + contentUrl 渲染 iframe', () => {
    mockedPreview.mockReturnValue(preview({ kind: 'pdf', status: 'ready', contentUrl: 'blob:pdf-1' }));

    render(<TaskArtifactPreviewDrawer artifact={fileArtifact({ name: '报告.pdf' })} context={context} onClose={jest.fn()} />);

    const frame = screen.getByTitle('报告.pdf 预览');
    expect(frame).toHaveAttribute('src', 'blob:pdf-1');

    // html 形态给 iframe 加 sandbox=""
    mockedPreview.mockReturnValue(preview({ kind: 'html', status: 'ready', contentUrl: 'blob:html-1' }));
    const second = render(<TaskArtifactPreviewDrawer artifact={fileArtifact({ name: '页面.html' })} context={context} onClose={jest.fn()} />);
    expect(screen.getByTitle('页面.html 预览')).toHaveAttribute('sandbox', '');
    second.unmount();
  });

  it('预览错误:展示 errorMessage', () => {
    mockedPreview.mockReturnValue(preview({ kind: 'other', status: 'error', errorMessage: '产物文件不存在或已被删除。' }));

    render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={jest.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('产物文件不存在或已被删除。');
  });

  it('预览就绪但无内容:展示兜底提示', () => {
    mockedPreview.mockReturnValue(preview({ kind: 'other', status: 'ready' }));

    render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={jest.fn()} />);

    expect(screen.getByText('暂无可展示的预览内容，请下载后查看。')).toBeInTheDocument();
  });

  it('链接产物缺地址时展示「未提供」且底部无「打开链接」入口', () => {
    render(
      <TaskArtifactPreviewDrawer
        artifact={{
          id: 'artifact-link',
          name: '需求链接',
          type: 'link',
          kind: 'url_link',
          updatedAt: '',
        }}
        context={context}
        onClose={jest.fn()}
      />,
    );

    const details = screen.getByTestId('task-artifact-link-details');
    expect(within(details).getAllByText('未提供').length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: '打开链接' })).not.toBeInTheDocument();
  });

  it('Escape 触发 onClose;aside 内 mousedown 因 stopPropagation 不触发遮罩关闭', () => {
    const onClose = jest.fn();
    render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={onClose} />);

    // 遮罩 mousedown → 关闭
    fireEvent.mouseDown(screen.getByTestId('task-artifact-preview-drawer'));
    expect(onClose).toHaveBeenCalledTimes(1);

    // aside 内 mousedown:stopPropagation 后不再冒泡到遮罩
    fireEvent.mouseDown(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalledTimes(1);

    // Escape 全局键
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('portalContainer 注入时弹层挂到宿主容器内', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);

    render(
      <TaskArtifactPreviewDrawer
        artifact={fileArtifact()}
        context={context}
        onClose={jest.fn()}
        portalContainer={host}
      />,
    );

    expect(within(host).getByRole('dialog')).toBeInTheDocument();
  });

  it('下载成功与失败:失败态展示错误信息', async () => {
    const { rerender } = render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={jest.fn()} />);

    // 下载成功(默认 mockResolvedValue):不弹错误提示
    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    await actFlush();
    expect(mockedDownload).toHaveBeenCalledWith(expect.objectContaining({ id: 'artifact-file' }), context);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    // Error 实例 → 展示 error.message
    mockedDownload.mockRejectedValueOnce(new Error('产物文件过大'));
    rerender(<TaskArtifactPreviewDrawer artifact={fileArtifact({ id: 'artifact-file-2' })} context={context} onClose={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('产物文件过大');

    // 非 Error 拒绝 → 展示兜底文案
    mockedDownload.mockRejectedValueOnce('boom');
    rerender(<TaskArtifactPreviewDrawer artifact={fileArtifact({ id: 'artifact-file-3' })} context={context} onClose={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('产物下载失败，请稍后重试。');
  });

  it('下载中:按钮置灰展示「下载中…」,完成后恢复', async () => {
    const deferred = createDeferred<void>();
    mockedDownload.mockReturnValueOnce(deferred.promise);
    render(<TaskArtifactPreviewDrawer artifact={fileArtifact()} context={context} onClose={jest.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
    const downloadingButton = screen.getByRole('button', { name: '下载中…' });
    expect(downloadingButton).toBeDisabled();

    deferred.resolve(undefined);
    expect(await screen.findByRole('button', { name: '下载文件' })).toBeEnabled();
  });

  // 说明:`if (typeof document === 'undefined') return null;`(源码 L233)在 jest jsdom
  // 环境下不可触达 —— jsdom Window 的 document 是 configurable:false 的访问器
  // (descriptor 证据:{get:fn, enumerable:true, configurable:false}),defineProperty
  // 与赋值均无法临时移除,详见任务报告。
});
