/** @jest-environment jsdom */
// 共享 Probe 属性类型:renderHook 泛型驱动,允许 rerender({ art: null })(覆盖率取消语义)。
type ProbeProps = { art: TaskArtifactView | null };
import {
  downloadTaskArtifact,
  useTaskArtifactPreview,
} from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { downloadTaskArtifactContent } from '@/services/backendApi/tasks/taskController';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import {
  getFileExt,
  getPreviewKind,
  prettifyJsonText,
} from '@/services/workspace/sessionFileUtils';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterAll, beforeAll, beforeEach } from '@jest/globals';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    previewResource: jest.fn(),
    downloadResource: jest.fn(),
  },
}));

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

jest.mock('@/services/workspace/sessionFileUtils', () => ({
  getPreviewKind: jest.fn(),
  getFileExt: jest.fn(),
  prettifyJsonText: jest.fn((text: string) => text),
  SESSION_FILE_TEXT_PREVIEW_MAX_BYTES: 1000,
}));

const mockedPreviewResource = botEditorService.previewResource as jest.MockedFunction<
  typeof botEditorService.previewResource
>;
const mockedDownloadResource = botEditorService.downloadResource as jest.MockedFunction<
  typeof botEditorService.downloadResource
>;
const mockedDownloadTaskArtifactContent =
  downloadTaskArtifactContent as jest.MockedFunction<typeof downloadTaskArtifactContent>;
const mockedBuildContentUrl = sessionFileService.buildContentUrl as jest.MockedFunction<
  typeof sessionFileService.buildContentUrl
>;
const mockedBuildDownloadUrl = sessionFileService.buildDownloadUrl as jest.MockedFunction<
  typeof sessionFileService.buildDownloadUrl
>;
const mockedFetchContentBlob = sessionFileService.fetchContentBlob as jest.MockedFunction<
  typeof sessionFileService.fetchContentBlob
>;
const mockGetPreviewKind = getPreviewKind as jest.MockedFunction<typeof getPreviewKind>;
const mockGetFileExt = getFileExt as jest.MockedFunction<typeof getFileExt>;
const mockPrettifyJsonText = prettifyJsonText as jest.MockedFunction<typeof prettifyJsonText>;

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

const BIG_CONTENT = 'x'.repeat(2000);

/** 带 `.text` 方法的 blob，用于触发 blobText 的 fast-path（L46 true 分支）；slice 同样带 `.text`。 */
function makeBlobWithText(content: string, max: number): Blob {
  const sliced: Blob = {
    size: Math.min(content.length, max),
    type: 'text/plain',
    text: () => Promise.resolve(content.slice(0, max)),
  } as unknown as Blob;
  return {
    size: content.length,
    type: 'text/plain',
    slice: () => sliced,
    text: () => Promise.resolve(content),
  } as unknown as Blob;
}

function makeArtifact(over: Partial<TaskArtifactView>): TaskArtifactView {
  return ({
    id: 'art-1',
    name: 'report.json',
    type: 'file',
    kind: 'file',
    updatedAt: '',
    fileRef: {
      storageType: 'object_file',
      storageId: 'sid',
      fileId: null,
      fileType: 'text/plain',
    },
    ...over,
  } as unknown) as TaskArtifactView;
}

function objectFileArtifact(over: Partial<TaskArtifactView> = {}): TaskArtifactView {
  return makeArtifact({
    name: 'report.pdf',
    fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'application/pdf' },
    ...over,
  });
}

function sessionFileArtifact(over: Partial<TaskArtifactView> = {}): TaskArtifactView {
  return makeArtifact({
    name: 'report.json',
    fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'application/json' },
    ...over,
  });
}

function workspaceArtifact(over: Partial<TaskArtifactView> = {}): TaskArtifactView {
  return makeArtifact({
    name: 'report.pdf',
    fileRef: { storageType: 'bot_workspace', storageId: 'wsid', fileId: null, fileType: 'application/pdf' },
    ...over,
  });
}

beforeAll(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn().mockReturnValue('blob:mock'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: jest.fn(),
  });
});

beforeEach(() => {
  mockedPreviewResource.mockReset();
  mockedDownloadResource.mockReset();
  mockedDownloadTaskArtifactContent.mockReset();
  mockedBuildContentUrl.mockReset();
  mockedBuildDownloadUrl.mockReset();
  mockedFetchContentBlob.mockReset();
  mockGetPreviewKind.mockReset();
  mockGetFileExt.mockReset();
  mockPrettifyJsonText.mockReset();

  mockGetFileExt.mockReturnValue('');
  mockPrettifyJsonText.mockImplementation((text: string) => text);
  mockedBuildContentUrl.mockReturnValue('http://content-url');
  mockedBuildDownloadUrl.mockReturnValue('http://download-url');
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('useTaskArtifactPreview - 状态分支', () => {
  it('artifact 为 null 时回退 unsupported', async () => {
    const { result } = renderHook(() => useTaskArtifactPreview(null, context));
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
    expect(result.current.kind).toBe('other');
  });

  it('text 形态直接就绪并计算 size', async () => {
    const artifact = makeArtifact({ kind: 'text', summary: 'hello', fileRef: undefined });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('hello');
    expect(result.current.size).toBe(new Blob(['hello']).size);
    expect(result.current.kind).toBe('text');
  });

  it('text 形态 summary 缺失兜底空串', async () => {
    const artifact = makeArtifact({ kind: 'text', summary: undefined, fileRef: undefined });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('');
  });

  it('kind=file 但 fileRef 缺失 unsupported', async () => {
    const artifact = makeArtifact({ fileRef: undefined });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
  });

  it('kind=file 但 storageId 为空 unsupported', async () => {
    const artifact = makeArtifact({
      fileRef: { storageType: 'object_file', storageId: '', fileId: null, fileType: '' },
    });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
  });

  it('getPreviewKind 返回 other 时 unsupported', async () => {
    mockGetPreviewKind.mockReturnValue('other');
    const artifact = objectFileArtifact({ name: 'thing.xyz' });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('unsupported'));
    expect(result.current.kind).toBe('other');
  });

  it('session_file 缺 fileId 报错 会话文件引用不完整', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    const artifact = sessionFileArtifact({
      fileRef: { storageType: 'session_file', storageId: 'sid', fileId: null, fileType: 'text/plain' },
    });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('会话文件引用不完整，无法预览。');
  });
});

describe('useTaskArtifactPreview - object_file', () => {
  it('text 形态走 FileReader 兜底 ready', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockResolvedValue(
      new Blob(['hello-text'], { type: 'text/plain' }),
    );
    const artifact = objectFileArtifact({ name: 'report.txt', fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('hello-text');
    expect(result.current.size).toBe('hello-text'.length);
    expect(result.current.truncated).toBe(false);
    expect(mockedDownloadTaskArtifactContent).toHaveBeenCalledWith('art-1');
  });

  it('非文本走 createObjectURL ready', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    mockedDownloadTaskArtifactContent.mockResolvedValue(
      new Blob(['pdf-bytes'], { type: 'application/pdf' }),
    );
    const artifact = objectFileArtifact();
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.kind).toBe('pdf');
    expect(result.current.contentUrl).toBe('blob:mock');
  });

  it.each([
    [401, '登录状态已失效，请重新登录后重试。'],
    [403, '无权访问该产物。'],
    [404, '产物文件不存在或已被删除。'],
    [413, '产物文件过大，请下载后查看。'],
  ])('加载失败 status=%i 触发 friendlyError 对应文案', async (status, message) => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockRejectedValue({ status });
    const artifact = objectFileArtifact({ name: 'report.txt', fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe(message);
  });

  it('加载失败无 status 走 preview 默认提示', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockRejectedValue(new Error('boom'));
    const artifact = objectFileArtifact({ name: 'report.txt', fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
  });
});

describe('useTaskArtifactPreview - session_file', () => {
  it('非文本走 buildContentUrl ready', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    const artifact = sessionFileArtifact({ name: 'report.pdf', fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'application/pdf' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(mockedBuildContentUrl).toHaveBeenCalledWith('sid', 'f1');
    expect(result.current.contentUrl).toBe('http://content-url');
    expect(mockedFetchContentBlob).not.toHaveBeenCalled();
  });

  it('文本 fetchContentBlob 失败报错', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedFetchContentBlob.mockResolvedValue({
      ok: false,
      error: { code: 'SESSION_FILE_DOWNLOAD_FORBIDDEN', friendlyMessage: '无权访问该文件。', canRetry: false },
    });
    const artifact = sessionFileArtifact({ name: 'report.txt', fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('无权访问该文件。');
    expect(mockedFetchContentBlob).toHaveBeenCalledWith('sid', 'f1');
  });

  it('文本 fetchContentBlob 成功且超长截断、json 美化、走 blob.text fast-path', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockGetFileExt.mockReturnValue('json');
    mockPrettifyJsonText.mockReturnValue('PRETTY');
    mockedFetchContentBlob.mockResolvedValue({ ok: true, data: makeBlobWithText(BIG_CONTENT, 1000) });
    const artifact = sessionFileArtifact({ name: 'report.json', fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'application/json' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.kind).toBe('text');
    expect(result.current.text).toBe('PRETTY');
    expect(result.current.truncated).toBe(true);
    expect(result.current.size).toBe(BIG_CONTENT.length);
    expect(result.current.contentUrl).toBe('http://content-url');
    expect(mockPrettifyJsonText).toHaveBeenCalledWith('x'.repeat(1000));
  });
});

describe('useTaskArtifactPreview - bot_workspace', () => {
  it('文本走 previewResource ready', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedPreviewResource.mockResolvedValue({
      kind: 'text',
      content: 'workspace-text',
      contentType: 'text/plain',
    } as any);
    const artifact = workspaceArtifact({ name: 'report.txt', fileRef: { storageType: 'bot_workspace', storageId: 'wsid', fileId: null, fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('workspace-text');
    expect(mockedPreviewResource).toHaveBeenCalledWith('bot-1', 'wsid', 'user-1');
  });

  it('文本 previewResource 返回非 text 形态抛错走 catch', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    mockedPreviewResource.mockResolvedValue({
      kind: 'image',
      blob: new Blob(['x']),
      contentType: 'image/png',
    } as any);
    const artifact = workspaceArtifact({ name: 'report.txt', fileRef: { storageType: 'bot_workspace', storageId: 'wsid', fileId: null, fileType: 'text/plain' } });
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
  });

  it('非文本走 downloadResource createObjectURL ready', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    mockedDownloadResource.mockResolvedValue(new Blob(['ws-bytes'], { type: 'application/pdf' }));
    const artifact = workspaceArtifact();
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.kind).toBe('pdf');
    expect(result.current.contentUrl).toBe('blob:mock');
    expect(mockedDownloadResource).toHaveBeenCalledWith('bot-1', 'wsid', 'user-1');
  });

  it('非文本 downloadResource 失败走 catch 预览默认提示', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    mockedDownloadResource.mockRejectedValue(new Error('boom'));
    const artifact = workspaceArtifact();
    const { result } = renderHook(() => useTaskArtifactPreview(artifact, context));
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
  });
});

describe('useTaskArtifactPreview - 取消（cancelled 守卫 false 分支）', () => {
  async function cancelObjectFileText(resolveWith: Blob): Promise<void> {
    mockGetPreviewKind.mockReturnValue('text');
    let resolveBlocker!: (v: Blob) => void;
    const blocker = new Promise<Blob>((r) => {
      resolveBlocker = r;
    });
    mockedDownloadTaskArtifactContent.mockReturnValue(blocker);
    const artifact = objectFileArtifact({
      name: 'report.txt',
      fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' },
    });
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker(resolveWith);
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  }

  it('object_file 文本加载中 rerender 触发取消，跳过 ready setState', async () => {
    await cancelObjectFileText(new Blob(['deferred'], { type: 'text/plain' }));
  });

  it('object_file 非文本加载中 rerender 触发取消，跳过 ready setState', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    let resolveBlocker!: (v: Blob) => void;
    const blocker = new Promise<Blob>((r) => {
      resolveBlocker = r;
    });
    mockedDownloadTaskArtifactContent.mockReturnValue(blocker);
    const artifact = objectFileArtifact();
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker(new Blob(['deferred'], { type: 'application/pdf' }));
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });

  it('session_file 文本 fetchContentBlob 失败时取消，跳过 error setState', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    let resolveBlocker!: (v: any) => void;
    const blocker = new Promise<any>((r) => {
      resolveBlocker = r;
    });
    mockedFetchContentBlob.mockReturnValue(blocker);
    const artifact = sessionFileArtifact({ name: 'report.txt', fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'text/plain' } });
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker({ ok: false, error: { code: 'X', friendlyMessage: '无权', canRetry: false } });
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });

  it('session_file 文本 fetchContentBlob 成功时取消，跳过 ready setState', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    let resolveBlocker!: (v: any) => void;
    const blocker = new Promise<any>((r) => {
      resolveBlocker = r;
    });
    mockedFetchContentBlob.mockReturnValue(blocker);
    const artifact = sessionFileArtifact({ name: 'report.txt', fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'text/plain' } });
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker({ ok: true, data: new Blob(['deferred'], { type: 'text/plain' }) });
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });

  it('bot_workspace 文本 previewResource 成功时取消，跳过 ready setState', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    let resolveBlocker!: (v: any) => void;
    const blocker = new Promise<any>((r) => {
      resolveBlocker = r;
    });
    mockedPreviewResource.mockReturnValue(blocker);
    const artifact = workspaceArtifact({ name: 'report.txt', fileRef: { storageType: 'bot_workspace', storageId: 'wsid', fileId: null, fileType: 'text/plain' } });
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker({ kind: 'text', content: 'deferred', contentType: 'text/plain' });
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });

  it('bot_workspace 非文本 downloadResource 成功时取消，跳过 ready setState', async () => {
    mockGetPreviewKind.mockReturnValue('pdf');
    let resolveBlocker!: (v: Blob) => void;
    const blocker = new Promise<Blob>((r) => {
      resolveBlocker = r;
    });
    mockedDownloadResource.mockReturnValue(blocker);
    const artifact = workspaceArtifact();
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      resolveBlocker(new Blob(['deferred'], { type: 'application/pdf' }));
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });

  it('object_file 加载抛错时取消，跳过 catch setState', async () => {
    mockGetPreviewKind.mockReturnValue('text');
    let rejectBlocker!: (e: any) => void;
    const blocker = new Promise<Blob>((_, rej) => {
      rejectBlocker = rej;
    });
    mockedDownloadTaskArtifactContent.mockReturnValue(blocker);
    const artifact = objectFileArtifact({ name: 'report.txt', fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' } });
    const { result, rerender, unmount } = renderHook<ReturnType<typeof useTaskArtifactPreview>, ProbeProps>(
      ({ art }) => useTaskArtifactPreview(art, context),
      { initialProps: { art: artifact } },
    );
    await waitFor(() => expect(result.current.status).toBe('loading'));
    rerender({ art: null });
    await act(async () => {
      rejectBlocker(new Error('boom'));
      await new Promise<void>((r) => { setTimeout(r, 20); });
    });
    expect(result.current.status).toBe('unsupported');
    unmount();
  });
});

describe('downloadTaskArtifact', () => {
  it('text 形态抛错不支持下载', async () => {
    const artifact = makeArtifact({ kind: 'text', summary: 'hi', fileRef: undefined });
    await expect(downloadTaskArtifact(artifact, context)).rejects.toThrow(
      '纯文本类型的产物不支持下载。',
    );
  });

  it('file 形态 fileRef 缺失抛错', async () => {
    const artifact = makeArtifact({ kind: 'file', fileRef: undefined });
    await expect(downloadTaskArtifact(artifact, context)).rejects.toThrow(
      '产物文件引用不完整，无法下载。',
    );
  });

  it('非 file 形态（url_link）fileRef 兜底为 null 抛错', async () => {
    const artifact = makeArtifact({ kind: 'url_link', fileRef: undefined });
    await expect(downloadTaskArtifact(artifact, context)).rejects.toThrow(
      '产物文件引用不完整，无法下载。',
    );
  });

  it('object_file 走后端代理下载', async () => {
    mockedDownloadTaskArtifactContent.mockResolvedValue(new Blob(['bytes'], { type: 'application/pdf' }));
    const clickSpy = jest.spyOn(HTMLElement.prototype, 'click').mockImplementation(() => {});
    const artifact = objectFileArtifact();
    await expect(downloadTaskArtifact(artifact, context)).resolves.toBeUndefined();
    expect(mockedDownloadTaskArtifactContent).toHaveBeenCalledWith('art-1');
    expect(clickSpy).toHaveBeenCalled();
    // 触发 window.setTimeout(,0) 的回收回调，覆盖 L209 箭头函数
    await new Promise<void>((r) => { setTimeout(r, 5); });
    clickSpy.mockRestore();
  });

  it('session_file 缺 fileId 抛错', async () => {
    const artifact = sessionFileArtifact({
      fileRef: { storageType: 'session_file', storageId: 'sid', fileId: null, fileType: 'application/pdf' },
    });
    await expect(downloadTaskArtifact(artifact, context)).rejects.toThrow(
      '会话文件引用不完整，无法下载。',
    );
  });

  it('session_file 走 buildDownloadUrl 新标签下载', async () => {
    const clickSpy = jest.spyOn(HTMLElement.prototype, 'click').mockImplementation(() => {});
    const artifact = sessionFileArtifact({
      name: 'report.pdf',
      fileRef: { storageType: 'session_file', storageId: 'sid', fileId: 'f1', fileType: 'application/pdf' },
    });
    await downloadTaskArtifact(artifact, context);
    expect(mockedBuildDownloadUrl).toHaveBeenCalledWith('sid', 'f1');
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it('bot_workspace downloadResource 失败抛 download 默认提示', async () => {
    mockedDownloadResource.mockRejectedValue(new Error('boom'));
    const artifact = workspaceArtifact();
    await expect(downloadTaskArtifact(artifact, context)).rejects.toThrow(
      '产物下载失败，请稍后重试。',
    );
  });

  it('bot_workspace downloadResource 成功走 blob 下载', async () => {
    mockedDownloadResource.mockResolvedValue(new Blob(['ws-bytes'], { type: 'application/pdf' }));
    const clickSpy = jest.spyOn(HTMLElement.prototype, 'click').mockImplementation(() => {});
    const artifact = workspaceArtifact();
    await expect(downloadTaskArtifact(artifact, context)).resolves.toBeUndefined();
    expect(mockedDownloadResource).toHaveBeenCalledWith('bot-1', 'wsid', 'user-1');
    expect(clickSpy).toHaveBeenCalled();
    // 触发 window.setTimeout(,0) 的回收回调，覆盖 L223 箭头函数
    await new Promise<void>((r) => { setTimeout(r, 5); });
    clickSpy.mockRestore();
  });
});

/**
 * blobText FileReader 兜底分支：
 *  - L49 `reader.result ?? ''` 右臂（result 为 null）
 *  - L50 onerror 箭头函数 + `reader.error ?? new Error(...)` 左/右臂
 * jsdom 的 Blob 没有 `.text`，走 FileReader；这里用自定义 FileReader 精确控制 onload/onerror 触发。
 */
describe('useTaskArtifactPreview - blobText FileReader 边界', () => {
  const objectFileTextArtifact = (): TaskArtifactView =>
    objectFileArtifact({
      name: 'report.txt',
      fileRef: { storageType: 'object_file', storageId: 'sid', fileId: null, fileType: 'text/plain' },
    });

  function fakeNoTextBlob(size = 4): Blob {
    return { size, type: 'text/plain', text: undefined } as unknown as Blob;
  }

  it('Reader result 为 null，触发 `?? ""` 兜底空串', async () => {
    const RealFileReader = global.FileReader;
    class NullResultReader {
      result: unknown = null;
      error: unknown = null;
      onload: ((e: Event) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      readAsText() {
        this.result = null;
        if (this.onload) this.onload(new Event('load'));
      }
    }
    (global as { FileReader: typeof FileReader }).FileReader = NullResultReader as unknown as typeof FileReader;

    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockResolvedValue(fakeNoTextBlob());
    const artifact = objectFileTextArtifact();
    const { result, unmount } = renderHook(() =>
      useTaskArtifactPreview(artifact, context),
    );
    try {
      await waitFor(() => expect(result.current.status).toBe('ready'));
      expect(result.current.text).toBe('');
    } finally {
      (global as { FileReader: typeof FileReader }).FileReader = RealFileReader;
      unmount();
    }
  });

  it('Reader onerror 且 error 非空，走 `reader.error ?? new Error(...)` 左臂', async () => {
    const RealFileReader = global.FileReader;
    class ErrorReader {
      result: unknown = null;
      error: unknown = null;
      onload: ((e: Event) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      readAsText() {
        this.error = new Error('read fail');
        if (this.onerror) this.onerror(new Event('error'));
      }
    }
    (global as { FileReader: typeof FileReader }).FileReader = ErrorReader as unknown as typeof FileReader;

    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockResolvedValue(fakeNoTextBlob());
    const artifact = objectFileTextArtifact();
    const { result, unmount } = renderHook(() =>
      useTaskArtifactPreview(artifact, context),
    );
    try {
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
    } finally {
      (global as { FileReader: typeof FileReader }).FileReader = RealFileReader;
      unmount();
    }
  });

  it('Reader onerror 且 error 为 null，走 `?? new Error("read artifact failed")` 右臂', async () => {
    const RealFileReader = global.FileReader;
    class NullErrorReader {
      result: unknown = null;
      error: unknown = null;
      onload: ((e: Event) => void) | null = null;
      onerror: ((e: Event) => void) | null = null;
      readAsText() {
        this.error = null;
        if (this.onerror) this.onerror(new Event('error'));
      }
    }
    (global as { FileReader: typeof FileReader }).FileReader = NullErrorReader as unknown as typeof FileReader;

    mockGetPreviewKind.mockReturnValue('text');
    mockedDownloadTaskArtifactContent.mockResolvedValue(fakeNoTextBlob());
    const artifact = objectFileTextArtifact();
    const { result, unmount } = renderHook(() =>
      useTaskArtifactPreview(artifact, context),
    );
    try {
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current.errorMessage).toBe('预览加载失败，请稍后重试。');
    } finally {
      (global as { FileReader: typeof FileReader }).FileReader = RealFileReader;
      unmount();
    }
  });
});
