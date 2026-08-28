/** @jest-environment jsdom */
import * as ctrl from '@/services/backendApi/bots/botSessionFileController';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botSessionFileController');
const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;

function file(name: string, size = 100): File {
  const f = new File(['x'.repeat(size)], name, { type: 'text/plain' });
  // jsdom File 缺少 arrayBuffer,补齐供 directUpload SINGLE 路径使用。
  (f as unknown as { arrayBuffer: () => Promise<ArrayBuffer> }).arrayBuffer = () =>
    Promise.resolve(new ArrayBuffer(size));
  // 补齐 slice，供 directUpload MULTIPART 路径切片断言使用。
  (f as unknown as { slice: (start?: number, end?: number, contentType?: string) => Blob }).slice = (
    start = 0,
    end = size,
    contentType = 'application/octet-stream',
  ) => new Blob(['x'.repeat(Math.max(0, end - start))], { type: contentType });
  return f;
}

describe('botSessionFileService.resolveContentUrl', () => {
  it('使用 gateway 内容地址', () => {
    mocked.buildBotSessionFileContentUrl.mockReturnValue('/openapi/v1/bots/bot-1/sessions/s1/files/sr_1/content?x=1');
    const url = botSessionFileService.resolveContentUrl('bot-1', 's1', 'sr_1', 'human_327325', '327325', 'attachment');
    expect(mocked.buildBotSessionFileContentUrl).toHaveBeenCalledWith(
      'bot-1',
      's1',
      'sr_1',
      { user_id: '327325', owner_id: '327325' },
      'attachment',
    );
    expect(url).toBe('/openapi/v1/bots/bot-1/sessions/s1/files/sr_1/content?x=1');
  });
});

describe('botSessionFileService.validateFiles', () => {
  it('白名单通过', () => {
    expect(botSessionFileService.validateFiles([file('a.pdf')], 20)).toBeNull();
  });
  it('超量提示', () => {
    expect(botSessionFileService.validateFiles([file('a.pdf'), file('b.pdf')], 1)).toContain('最多上传');
  });
  it('不支持类型', () => {
    expect(botSessionFileService.validateFiles([file('a.exe')], 20)).toContain('类型不支持');
  });
});

describe('botSessionFileService.uploadOne', () => {
  beforeEach(() => {
    [
      mocked.createUploadIntents,
      mocked.uploadToUrl,
      mocked.completeUpload,
      mocked.getMaterializeStatus,
      mocked.listReady,
      mocked.deleteFile,
      mocked.getContentBlob,
    ].forEach((m) => m?.mockClear?.());
    jest.useFakeTimers();
    mocked.createUploadIntents.mockResolvedValue({
      files: [
        {
          resource_id: 'sr_1',
          display_name: 'a.pdf',
          status: 'upload_url_issued',
          size_bytes: 100,
          content_hash: null,
          task_version: null,
          error_code: null,
          transfer_id: 'tr_1',
          upload_type: 'SINGLE',
          http_method: 'PUT',
          upload_url: 'https://oss/put',
          expires_at: null,
          upload_session_id: null,
          part_size: null,
          part_count: null,
          parts: null,
        },
      ],
    });
    mocked.uploadToUrl.mockResolvedValue(undefined);
    mocked.completeUpload.mockResolvedValue({
      resource_id: 'sr_1',
      display_name: 'a.pdf',
      status: 'device_syncing',
      size_bytes: 100,
      content_hash: null,
      task_version: null,
      error_code: null,
    });
    mocked.getMaterializeStatus.mockResolvedValue({
      resource_id: 'sr_1',
      display_name: 'a.pdf',
      status: 'ready',
      size_bytes: 100,
      content_hash: null,
      task_version: null,
      error_code: null,
    });
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('完整上传后轮询到 ready', async () => {
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    await jest.advanceTimersByTimeAsync(2000);
    const res = await pending;
    expect(res.ok).toBe(true);
    expect((res as { data: { resourceId: string; status: string } }).data.resourceId).toBe('sr_1');
    expect((res as { data: { resourceId: string; status: string } }).data.status).toBe('ready');
    expect(mocked.uploadToUrl).toHaveBeenCalledWith(
      'https://oss/put',
      'PUT',
      expect.any(ArrayBuffer),
      expect.any(Object),
    );
    expect(mocked.completeUpload).toHaveBeenCalledWith('bot-1', 'sess-1', expect.any(Object), {
      resource_id: 'sr_1',
      transfer_id: 'tr_1',
    });
  });

  it('MULTIPART 按 offset/size 切分并透传 part headers', async () => {
    mocked.completeUpload.mockResolvedValueOnce({
      resource_id: 'sr_1',
      display_name: 'a.pdf',
      status: 'ready',
      size_bytes: 100,
      content_hash: null,
      task_version: null,
      error_code: null,
    });
    mocked.createUploadIntents.mockResolvedValueOnce({
      files: [
        {
          resource_id: 'sr_1',
          display_name: 'a.pdf',
          status: 'upload_url_issued',
          size_bytes: 100,
          content_hash: null,
          task_version: null,
          error_code: null,
          transfer_id: 'tr_1',
          upload_type: 'MULTIPART',
          http_method: 'PUT',
          upload_url: null,
          expires_at: null,
          upload_session_id: null,
          part_size: 50,
          part_count: 2,
          parts: [
            {
              part_number: 1,
              upload_url: 'https://oss/part-1',
              offset: 0,
              size: 50,
              headers: { 'x-upload-part-token': 'token-1' },
            },
            { part_number: 2, upload_url: 'https://oss/part-2', offset: 50, size: 50 },
          ],
        },
      ],
    });

    const res = await botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf', 100), {});

    expect(res.ok).toBe(true);
    expect(mocked.uploadToUrl).toHaveBeenCalledTimes(2);
    expect(mocked.uploadToUrl).toHaveBeenNthCalledWith(
      1,
      'https://oss/part-1',
      'PUT',
      expect.objectContaining({ size: 50 }),
      expect.objectContaining({ headers: { 'x-upload-part-token': 'token-1' } }),
    );
    expect(mocked.uploadToUrl).toHaveBeenNthCalledWith(
      2,
      'https://oss/part-2',
      'PUT',
      expect.objectContaining({ size: 50 }),
      expect.any(Object),
    );
  });

  it('completeUpload 直接 ready 不轮询', async () => {
    mocked.completeUpload.mockResolvedValueOnce({
      resource_id: 'sr_1',
      display_name: 'a.pdf',
      status: 'ready',
      size_bytes: 100,
      content_hash: null,
      task_version: null,
      error_code: null,
    });
    const res = await botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    expect(res.ok).toBe(true);
    expect(mocked.getMaterializeStatus).not.toHaveBeenCalled();
  });

  it('物化失败返回错误', async () => {
    mocked.getMaterializeStatus.mockResolvedValueOnce({
      resource_id: 'sr_1',
      display_name: 'a.pdf',
      status: 'device_sync_failed',
      size_bytes: 100,
      content_hash: null,
      task_version: null,
      error_code: 'sync_err',
    });
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    await jest.advanceTimersByTimeAsync(2000);
    const res = await pending;
    expect(res.ok).toBe(false);
    expect((res as { error: { friendlyMessage: string } }).error.friendlyMessage).toContain('物化失败');
  });
});

describe('botSessionFileService 列表/删除/下载', () => {
  it('loadReady 映射 view', async () => {
    mocked.listReady.mockResolvedValue({
      files: [
        {
          resource_id: 'sr_1',
          display_name: 'a.pdf',
          status: 'ready',
          size_bytes: 1,
          content_hash: null,
          task_version: null,
          error_code: null,
        },
      ],
    });
    const res = await botSessionFileService.loadReady('bot-1', 'sess-1', 'user-1');
    expect(res.ok).toBe(true);
    expect((res as { data: { items: { displayName: string }[] } }).data.items[0].displayName).toBe('a.pdf');
  });
  it('loadReady 将 human_ 前缀的 user_id 归一化为工号', async () => {
    mocked.listReady.mockResolvedValue({ files: [] });
    await botSessionFileService.loadReady('bot-1', 'sess-1', 'human_327325');
    expect(mocked.listReady).toHaveBeenCalledWith('bot-1', 'sess-1', { user_id: '327325' });
  });
  it('remove 成功', async () => {
    mocked.deleteFile.mockResolvedValue({ deleted: true });
    const res = await botSessionFileService.remove('bot-1', 'sess-1', 'sr_1', 'user-1');
    expect(res.ok).toBe(true);
  });
});
