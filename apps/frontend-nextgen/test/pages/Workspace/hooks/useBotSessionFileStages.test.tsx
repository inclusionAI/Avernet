/** @jest-environment jsdom */
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { botSessionFileService, isLargeBotSessionFile } from '@/services/workspace/botSessionFileService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionFileService');
jest.mock('sonner', () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
const service = jest.mocked(botSessionFileService);
const file = {
  resourceId: 'r1',
  displayName: 'image.png',
  status: 'ready' as const,
  sizeBytes: 1,
  errorCode: null,
  creatorType: null,
  creatorName: null,
  createdAt: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  service.loadReady.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  service.remove.mockResolvedValue({ ok: true, data: true });
  service.resolveContentUrl.mockReturnValue('/content');
  service.resolveExternalDownloadUrl.mockResolvedValue('/external');
  jest.mocked(isLargeBotSessionFile).mockReturnValue(false);
  jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  jest.spyOn(window, 'open').mockImplementation(() => null);
});
afterEach(() => jest.restoreAllMocks());

it.each(
  (['draft', 'verify', 'online', undefined] as const).flatMap((stage) =>
    [undefined, 'human_friend'].map((friendUserId) => ({ stage, friendUserId }))),
)('文件列表/删除/下载/预览 stage=$stage friend=$friendUserId', async ({ stage, friendUserId }) => {
  const { result } = renderHook(() => useBotSessionFiles('bot', 's1', 'viewer', 'entity', friendUserId, stage));
  await act(async () => {
    await result.current.refresh();
  });
  expect(service.loadReady).toHaveBeenCalledWith('bot', 's1', 'viewer', 'entity', friendUserId, stage, undefined);
  await act(async () => {
    await result.current.deleteFile(file);
  });
  expect(service.remove).toHaveBeenCalledWith('bot', 's1', 'r1', 'viewer', 'entity', stage, undefined, friendUserId);
  await act(async () => {
    await result.current.downloadFile(file);
  });
  expect(service.resolveContentUrl).toHaveBeenLastCalledWith(
    'bot',
    's1',
    'r1',
    'viewer',
    'entity',
    'attachment',
    stage,
    undefined,
    friendUserId,
  );
  await act(async () => {
    await result.current.previewFile(file);
  });
  expect(service.resolveContentUrl).toHaveBeenLastCalledWith('bot', 's1', 'r1', 'viewer', 'entity', 'inline', stage, undefined, friendUserId);
  jest.mocked(isLargeBotSessionFile).mockReturnValue(true);
  await act(async () => {
    await result.current.downloadFile(file);
  });
  expect(service.resolveExternalDownloadUrl).toHaveBeenLastCalledWith('bot', 's1', 'r1', 'viewer', 'entity', stage, undefined, friendUserId);
});

it('文件预览内容 URL 与文本读取均使用 online', async () => {
  const textFile = { ...file, displayName: 'note.txt' };
  service.fetchBlob.mockResolvedValue({ ok: true, data: { size: 4, text: async () => 'text' } as Blob });
  const { result } = renderHook(() =>
    useBotSessionFilePreview(textFile, {
      botId: 'bot',
      sessionId: 's1',
      userId: 'viewer',
      ownerId: 'entity',
      stage: 'online',
      friendUserId: 'human_friend',
    }),
  );
  await waitFor(() => expect(result.current.status).toBe('ready'));
  // 尾位 friendUserId 槽(2026-11-19 需求追加)显式占位,与调用形态逐位对齐。
  expect(service.resolveContentUrl).toHaveBeenLastCalledWith('bot', 's1', 'r1', 'viewer', 'entity', 'inline', 'online', undefined, 'human_friend');
  expect(service.fetchBlob).toHaveBeenLastCalledWith('bot', 's1', 'r1', 'viewer', 'entity', 'inline', 'online', undefined, 'human_friend');
});
