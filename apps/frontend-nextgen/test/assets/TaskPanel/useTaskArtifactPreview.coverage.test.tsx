/** @jest-environment jsdom */
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// useTaskArtifactPreview 行覆盖补全:objectFile 专测之外的全部形态——
// text/url 形态、session_file 预览(image/Web 内容链接/text blob 二端)、bot workspace 预览(text/image)、
// friendlyError 全状态码、取消竞态、revoke 清理,以及 downloadTaskArtifact 各存储路由。
jest.mock('@/services/backendApi/tasks/taskController', () => ({
  downloadTaskArtifactContent: jest.fn(),
}));
jest.mock('@/services/workspace/sessionFileService', () => ({
  sessionFileService: {
    buildContentUrl: jest.fn(),
    buildDownloadUrl: jest.fn(),
    fetchContentBlob: jest.fn(),
  },
}));
jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    previewResource: jest.fn(),
    downloadResource: jest.fn(),
  },
}));

import {
  downloadTaskArtifact,
  useTaskArtifactPreview,
} from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { downloadTaskArtifactContent } from '@/services/backendApi/tasks/taskController';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { renderHook, waitFor } from '@testing-library/react';

const mockedDownloadContent = downloadTaskArtifactContent as jest.MockedFunction<
  typeof downloadTaskArtifactContent
>;
const mockedFiles = sessionFileService as unknown as Record<string, jest.Mock>;
const mockedBotEditor = botEditorService as unknown as Record<string, jest.Mock>;

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

/** 可控 Deferred:驱动取消竞态用例。 */
interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((r, rj) => {
    resolve = r;
    reject = rj;
  });
  return { promise, resolve, reject };
}

function artifact(overrides: Record<string, unknown>): TaskArtifactView {
  return {
    id: 'art-1',
    name: 'a.bin',
    type: 'file',
    kind: 'file',
    updatedAt: '',
    fileRef: null,
    ...overrides,
  } as TaskArtifactView;
}

// jsdom 的 Blob 未实现 text() → blobText 走 FileReader 兜底;
// 这里同时给一个带 text() 的伪 Blob,覆盖两条读路径。
function blobWithText(text: string, size: number): Blob {
  return { size, type: 'text/plain', text: async () => text } as unknown as Blob;
}

/**
 * 产物对象必须只构造一次再进 renderHook:测试回调会在每次 render 重新执行
 * (renderHook 内部 TestComponent 重渲染,return 都会调一次 callback),
 * 内联字面量会让 fileRef 引用逐帧变化 → effect deps 变化 → setState 无限重排。
 */
function renderPreview(overrides: Record<string, unknown>) {
  const art = artifact(overrides);
  return renderHook(() => useTaskArtifactPreview(art, context));
}

let clickSpy: jest.SpiedFunction<() => void>;

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn(() => 'blob:preview-url'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn(),
  });
  clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click');

  mockedDownloadContent.mockReset();
  mockedDownloadContent.mockResolvedValue(new Blob(['proxy-bytes']));
  mockedFiles.buildContentUrl.mockReset().mockReturnValue('/openapi/v1/session-files/content');
  mockedFiles.buildDownloadUrl.mockReset().mockReturnValue('/openapi/v1/session-files/download');
  mockedFiles.fetchContentBlob.mockReset().mockResolvedValue({ ok: true, data: new Blob(['file-body']) });
  mockedBotEditor.previewResource.mockReset();
  mockedBotEditor.downloadResource.mockReset();
});

afterEach(() => {
  clickSpy.mockRestore();
});

// ── 预览 ───────────────────────────────────────────────────────────────────

describe('useTaskArtifactPreview 覆盖补全', () => {
  it('artifact 为空 → unsupported', async () => {
    const { result } = renderHook(() => useTaskArtifactPreview(null, context));
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
    expect(result.current.kind).toBe('other');
  });

  it('text 形态:summary 直接进预览,size 按字节数', async () => {
    const { result } = renderPreview({ kind: 'text', summary: '纯文本' });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('纯文本');
    expect(result.current.kind).toBe('text');
    expect(result.current.size).toBe(9); // 3 个汉字 × 3 字节
  });

  it('summary 缺省的 text 形态 → 空文本 ready', async () => {
    const { result } = renderPreview({ kind: 'text', summary: null });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('');
    expect(result.current.size).toBe(0);
  });

  it('file 形态无 fileRef → unsupported', async () => {
    const { result } = renderPreview({ kind: 'file' });
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
  });

  it('fileRef 缺 storageId(脏数据) → unsupported', async () => {
    const { result } = renderPreview({ kind: 'file', name: 'note.txt', fileRef: { storageType: 'session_file', storageId: null, fileId: 'f-1', fileType: 'text/plain' }, });
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
  });

  it('不可预览扩展名(exe) → other/unsupported', async () => {
    const { result } = renderPreview({ name: 'archive.exe', fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/archive.exe', fileId: null }, });
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
    expect(result.current.kind).toBe('other');
  });

  it('session_file 引用缺 fileId → 预览 error 且给出可读文案', async () => {
    const { result } = renderPreview({ name: 'note.txt', fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: null, fileType: 'text/plain' }, });
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('会话文件引用不完整，无法预览。');
  });

  describe('object_file 预览', () => {
    it('text 文件:后端代理取字节 + json 美化(blob.text 路径)', async () => {
      mockedDownloadContent.mockResolvedValue(blobWithText('{"a":1}', 7));
      const { result } = renderPreview({ name: 'result.json', fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/result.json', fileId: null }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(mockedDownloadContent).toHaveBeenCalledWith('art-1');
      expect(result.current.text).toBe('{\n  "a": 1\n}');
      expect(result.current.size).toBe(7);
      expect(result.current.contentUrl).toBeNull();
    });

    it('非 text 文件:走 URL.createObjectURL 出内容链接,卸载时 revoke', async () => {
      const { result, unmount } = renderPreview({ name: 'report.pdf', fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/report.pdf', fileId: null }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(result.current.contentUrl).toBe('blob:preview-url');
      expect(URL.createObjectURL as jest.Mock).toHaveBeenCalled();

      // 卸载清理 objectUrl
      unmount();
      expect(URL.revokeObjectURL as jest.Mock).toHaveBeenCalledWith('blob:preview-url');
    });
  });

  describe('session_file 预览', () => {
    it('image 等非 text:只构造内容链接,不拉字节', async () => {
      const { result } = renderPreview({ name: 'img.png', fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: 'f-1', fileType: 'image/png' }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(mockedFiles.buildContentUrl).toHaveBeenCalledWith('sess-1', 'f-1');
      expect(result.current.contentUrl).toBe('/openapi/v1/session-files/content');
      expect(mockedFiles.fetchContentBlob).not.toHaveBeenCalled();
    });

    it('text 文件:fetchContentBlob 失败 → error 透出 friendlyMessage', async () => {
      mockedFiles.fetchContentBlob.mockResolvedValue({
        ok: false,
        error: { friendlyMessage: '无权访问该文件。' },
      });
      const { result } = renderPreview({ name: 'note.txt', fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: 'f-1', fileType: 'text/plain' }, });
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe('无权访问该文件。');
    });

    it('text 文件成功:链接 + 文本内容双输出(FileReader 读路径)', async () => {
      mockedFiles.fetchContentBlob.mockResolvedValue({ ok: true, data: new Blob(['file-body']) });
      const { result } = renderPreview({ name: 'note.txt', fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: 'f-1', fileType: 'text/plain' }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      // jsdom Blob 无 text() → FileReader 兜底读到字节
      expect(result.current.text).toBe('file-body');
      expect(result.current.contentUrl).toBe('/openapi/v1/session-files/content');
      expect(result.current.size).toBeGreaterThan(0);
    });

    it('text 文件:FileReader 兜底读取失败 → reject 进错误态', async () => {
      mockedFiles.fetchContentBlob.mockResolvedValue({ ok: true, data: new Blob(['file-body']) });
      // 拦截 readAsText:同步触发 onerror(blobText 读路径会先挂 onerror 再 read)
      const readSpy = jest.spyOn(FileReader.prototype, 'readAsText').mockImplementation(function fakeRead(
        this: FileReader,
      ) {
        const onerror = (this as unknown as { onerror?: ((e: unknown) => void) | null }).onerror;
        onerror?.(new Event('error'));
      });
      const { result } = renderPreview({ name: 'note.txt', fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: 'f-1', fileType: 'text/plain' }, });
      await waitFor(() => expect(result.current.status).toBe('error'));
      // reader.error 为 null → reject(new Error('read artifact failed')) → friendlyError 无 status → 通用文案
      expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
      readSpy.mockRestore();
    });
  });

  describe('bot workspace 预览', () => {
    it('text 文件:previewResource 返回 text → ready', async () => {
      mockedBotEditor.previewResource.mockResolvedValue({
        kind: 'text',
        content: '# readme',
        contentType: 'text/markdown',
      });
      const { result } = renderPreview({ name: 'readme.md', fileRef: { storageType: 'bot_workspace', storageId: 'bot-1/ws/readme.md', fileId: null }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(mockedBotEditor.previewResource).toHaveBeenCalledWith('bot-1', 'bot-1/ws/readme.md', 'user-1');
      expect(result.current.text).toBe('# readme');
    });

    it('text 文件:previewResource 返回非 text 形态 → reject 进错误态', async () => {
      mockedBotEditor.previewResource.mockResolvedValue({ kind: 'image', blob: new Blob(['x']) });
      const { result } = renderPreview({ name: 'readme.md', fileRef: { storageType: 'bot_workspace', storageId: 'bot-1/ws/readme.md', fileId: null }, });
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
    });

    it('非 text 文件:downloadResource 返回 Blob → 内容链接', async () => {
      mockedBotEditor.downloadResource.mockResolvedValue(new Blob(['image-bytes'], { type: 'image/png' }));
      const { result } = renderPreview({ name: 'photo.png', fileRef: { storageType: 'bot_workspace', storageId: 'bot-1/ws/photo.png', fileId: null }, });
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(result.current.contentUrl).toBe('blob:preview-url');
      expect(result.current.size).toBe(11);
    });
  });

  describe('friendlyError 全状态码(object_file 代理取字节失败)', () => {
    const textFileArtifact = artifact({
      name: 'note.txt',
      fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/note.txt', fileId: null },
    });

    it.each([
      [401, '登录状态已失效，请重新登录后重试。'],
      [403, '无权访问该产物。'],
      [404, '产物文件不存在或已被删除。'],
      [413, '产物文件过大，请下载后查看。'],
    ])('status=%d → %s', async (status, message) => {
      mockedDownloadContent.mockRejectedValue(Object.assign(new Error('x'), { status }));
      const { result } = renderHook(() => useTaskArtifactPreview(textFileArtifact, context));
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe(message);
    });

    it('无 status 的普通错误 → 通用预览失败文案', async () => {
      mockedDownloadContent.mockRejectedValueOnce(new Error('socket reset'));
      const { result } = renderHook(() => useTaskArtifactPreview(textFileArtifact, context));
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
    });
  });

  it('切换产物:上一轮异步未完成即被取消,迟到数据不写入', async () => {
    const gate = deferred<Blob>();
    mockedDownloadContent.mockReturnValueOnce(gate.promise);
    const pdfArtifact = artifact({
      name: 'report.pdf',
      fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/report.pdf', fileId: null },
    });
    const { result, rerender } = renderHook(
      ({ art }: { art: TaskArtifactView | null }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: pdfArtifact as TaskArtifactView | null } },
    );

    // 上一轮还在 loading 时切到空产物 → effect 清理置 cancelled
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await waitFor(() => expect(result.current.status).toBe('unsupported'));

    // 迟到的旧产物字节到达 → 已取消,不再回写 ready 态
    gate.resolve(new Blob(['late-bytes']));
    await waitFor(() => expect(mockedDownloadContent).toHaveBeenCalledTimes(1));
    await new Promise((r) => {
      setTimeout(r, 0);
    });
    expect(result.current.status).toBe('unsupported');
  });
});

// ── 下载 ───────────────────────────────────────────────────────────────────

describe('downloadTaskArtifact 下载路由', () => {
  it('text 形态直接拒绝', async () => {
    await expect(
      downloadTaskArtifact(artifact({ kind: 'text', summary: 'x' }), context),
    ).rejects.toThrow('纯文本类型的产物不支持下载。');
  });

  it('非 file 形态(url_link)无 fileRef → fileRef 缺失拒绝', async () => {
    await expect(downloadTaskArtifact(artifact({ kind: 'url_link' }), context)).rejects.toThrow(
      '产物文件引用不完整，无法下载。',
    );
  });

  it('object_file:代理取字节 + anchor 下载 + 定时 revoke', async () => {
    await downloadTaskArtifact(
      artifact({
        name: 'report.pdf',
        fileRef: { storageType: 'object_file', storageId: 'task-artifacts/1/report.pdf', fileId: null },
      }),
      context,
    );
    expect(mockedDownloadContent).toHaveBeenCalledWith('art-1');
    expect(clickSpy).toHaveBeenCalled();
    // 让 window.setTimeout(0) 的 revoke 回调真正跑一轮宏任务
    await new Promise((r) => {
      setTimeout(r, 1);
    });
    expect(URL.revokeObjectURL as jest.Mock).toHaveBeenCalledWith('blob:preview-url');
  });

  it('session_file 缺 fileId → 拒绝', async () => {
    await expect(
      downloadTaskArtifact(
        artifact({
          name: 'note.txt',
          fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: null },
        }),
        context,
      ),
    ).rejects.toThrow('会话文件引用不完整，无法下载。');
  });

  it('session_file 有 fileId:导航下载链接 + 新标签打开', async () => {
    await downloadTaskArtifact(
      artifact({
        name: 'note.txt',
        fileRef: { storageType: 'session_file', storageId: 'sess-1', fileId: 'f-1' },
      }),
      context,
    );
    expect(mockedFiles.buildDownloadUrl).toHaveBeenCalledWith('sess-1', 'f-1');
    expect(clickSpy).toHaveBeenCalled();
  });

  it('bot workspace:Blob 下载成功', async () => {
    mockedBotEditor.downloadResource.mockResolvedValue(new Blob(['ws-bytes']));
    await downloadTaskArtifact(
      artifact({
        name: 'doc.pdf',
        fileRef: { storageType: 'bot_workspace', storageId: 'bot-1/ws/doc.pdf', fileId: null },
      }),
      context,
    );
    expect(mockedBotEditor.downloadResource).toHaveBeenCalledWith('bot-1', 'bot-1/ws/doc.pdf', 'user-1');
    expect(clickSpy).toHaveBeenCalled();
    // workspace 路径同样按 0ms 定时 revoke 临时 objectUrl
    await new Promise((r) => {
      setTimeout(r, 1);
    });
    expect(URL.revokeObjectURL as jest.Mock).toHaveBeenCalledWith('blob:preview-url');
  });

  it('bot workspace 下载失败(403) → 译成可读文案抛出', async () => {
    mockedBotEditor.downloadResource.mockRejectedValue(Object.assign(new Error('x'), { status: 403 }));
    await expect(
      downloadTaskArtifact(
        artifact({
          name: 'doc.pdf',
          fileRef: { storageType: 'bot_workspace', storageId: 'bot-1/ws/doc.pdf', fileId: null },
        }),
        context,
      ),
    ).rejects.toThrow('无权访问该产物。');
  });
});
