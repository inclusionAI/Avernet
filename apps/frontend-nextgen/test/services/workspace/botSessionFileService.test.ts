/** @jest-environment jsdom */
import * as ctrl from '@/services/backendApi/bots/botSessionFileController';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
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
    const url = botSessionFileService.resolveContentUrl('bot-1', 's1', 'sr_1', 'human_900003', '900003', 'attachment');
    expect(mocked.buildBotSessionFileContentUrl).toHaveBeenCalledWith(
      'bot-1',
      's1',
      'sr_1',
      { user_id: '900003', owner_id: '900003' },
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

  it('onPhase 依序上报 completing → materializing,供 UI 展示物化中转圈', async () => {
    const onPhase = jest.fn();
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), { onPhase });
    await jest.advanceTimersByTimeAsync(2000);
    const res = await pending;
    expect(res.ok).toBe(true);
    expect(onPhase.mock.calls.map((c: [('completing' | 'materializing')]) => c[0])).toEqual([
      'completing',
      'materializing',
    ]);
  });

  it('物化轮询遇 5xx 容忍继续,后续 ready 仍成功', async () => {
    mocked.getMaterializeStatus.mockRejectedValueOnce(
      Object.assign(new Error('bad gateway'), { status: 502 }),
    );
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    await jest.advanceTimersByTimeAsync(5000);
    const res = await pending;
    expect(res.ok).toBe(true);
    expect(mocked.getMaterializeStatus).toHaveBeenCalledTimes(2);
  });

  it('物化轮询遇 4xx 致命,立即失败不再重试', async () => {
    mocked.getMaterializeStatus.mockRejectedValueOnce(
      Object.assign(new Error('resource gone'), { status: 404 }),
    );
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    await jest.advanceTimersByTimeAsync(5000);
    const res = await pending;
    expect(res.ok).toBe(false);
    expect((res as { error: { friendlyMessage: string } }).error.friendlyMessage).toBe('resource gone');
    expect(mocked.getMaterializeStatus).toHaveBeenCalledTimes(1);
  });
});

describe('botSessionFileService 列表/删除/下载', () => {
  it('好友文件列表传递规范化的 f_user_id，保留 Bot owner_id', async () => {
    mocked.listReady.mockResolvedValue({ files: [] });
    await botSessionFileService.loadReady('bot-1', 'sess-1', 'human_149608', '146836', 'human_149608');
    expect(mocked.listReady).toHaveBeenCalledWith('bot-1', 'sess-1', {
      user_id: '149608',
      owner_id: '146836',
      f_user_id: '149608',
    });
  });
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
  it('loadReady 透传创建者字段（2026-11-09 契约），旧数据缺字段归一 null', async () => {
    mocked.listReady.mockResolvedValue({
      files: [
        {
          resource_id: 'sr_1',
          display_name: 'report.txt',
          status: 'ready',
          size_bytes: 128,
          content_hash: null,
          task_version: null,
          error_code: null,
          creator_type: 'bot',
          creator_name: '报告助手',
          created_at: '2026-11-09T02:15:30.123456Z',
        },
        {
          // 旧引擎/旧网关缺创建者字段 → View 归一 null，不猜测。
          resource_id: 'sr_2',
          display_name: 'input.csv',
          status: 'ready',
          size_bytes: 1024,
          content_hash: null,
          task_version: null,
          error_code: null,
        },
      ],
    });
    const res = await botSessionFileService.loadReady('bot-1', 'sess-1', 'user-1');
    expect(res.ok).toBe(true);
    const items = (res as { data: { items: BotSessionFileView[] } }).data.items;
    expect(items[0]).toMatchObject({
      creatorType: 'bot',
      creatorName: '报告助手',
      createdAt: '2026-11-09T02:15:30.123456Z',
    });
    expect(items[1]).toMatchObject({ creatorType: null, creatorName: null, createdAt: null });
  });
  it('loadReady 将 human_ 前缀的 user_id 归一化为工号', async () => {
    mocked.listReady.mockResolvedValue({ files: [] });
    await botSessionFileService.loadReady('bot-1', 'sess-1', 'human_900003');
    expect(mocked.listReady).toHaveBeenCalledWith('bot-1', 'sess-1', { user_id: '900003' });
  });
  it('remove 成功', async () => {
    mocked.deleteFile.mockResolvedValue({ deleted: true });
    const res = await botSessionFileService.remove('bot-1', 'sess-1', 'sr_1', 'user-1');
    expect(res.ok).toBe(true);
  });
});

describe('会话文件 stage 和好友身份贯穿读写与内容地址', () => {
  it.each(
    (['draft', 'verify', 'online'] as const).flatMap((stage) =>
      [undefined, 'human_viewer'].map((friendUserId) => ({ stage, friendUserId }))),
  )('stage=$stage friend=$friendUserId 保留各参数位置', async ({ stage, friendUserId }) => {
    mocked.listReady.mockResolvedValue({ files: [] });
    mocked.deleteFile.mockResolvedValue({ deleted: true });
    mocked.getContentBlob.mockResolvedValue(new Blob(['text']));
    const params = {
      user_id: 'viewer', owner_id: 'entity', stage,
      ...(friendUserId ? { f_user_id: 'viewer' } : {}),
    };
    await botSessionFileService.loadReady('bot', 's1', 'human_viewer', 'entity', friendUserId, stage);
    expect(mocked.listReady).toHaveBeenLastCalledWith('bot', 's1', params);
    await botSessionFileService.remove('bot', 's1', 'r1', 'human_viewer', 'entity', stage, undefined, friendUserId);
    expect(mocked.deleteFile).toHaveBeenLastCalledWith('bot', 's1', 'r1', params);
    await botSessionFileService.fetchBlob('bot', 's1', 'r1', 'human_viewer', 'entity', 'inline', stage, undefined, friendUserId);
    expect(mocked.getContentBlob).toHaveBeenLastCalledWith('bot', 's1', 'r1', params, 'inline');
    botSessionFileService.resolveContentUrl('bot', 's1', 'r1', 'human_viewer', 'entity', 'attachment', stage, undefined, friendUserId);
    expect(mocked.buildBotSessionFileContentUrl).toHaveBeenLastCalledWith('bot', 's1', 'r1', params, 'attachment');
  });
});

describe('device_uuid 拼装 - 多实例选中实例定位(openspec add-session-files-device-uuid)', () => {
  beforeEach(() => {
    [
      mocked.createUploadIntents,
      mocked.uploadToUrl,
      mocked.completeUpload,
      mocked.getMaterializeStatus,
      mocked.listReady,
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

  it('uploadOne 传 deviceUuid 时上传链三接口 params 均带 device_uuid', async () => {
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {
      deviceUuid: 'DEVICE-A',
    });
    await jest.advanceTimersByTimeAsync(2000);
    const res = await pending;
    expect(res.ok).toBe(true);
    expect(mocked.createUploadIntents.mock.calls[0][2]).toEqual(
      expect.objectContaining({ device_uuid: 'DEVICE-A' }),
    );
    expect(mocked.completeUpload.mock.calls[0][2]).toEqual(
      expect.objectContaining({ device_uuid: 'DEVICE-A' }),
    );
    expect(mocked.getMaterializeStatus.mock.calls[0][3]).toEqual(
      expect.objectContaining({ device_uuid: 'DEVICE-A' }),
    );
  });

  it('uploadOne 不传 deviceUuid 时上传链 params 无该键', async () => {
    const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'user-1', file('a.pdf'), {});
    await jest.advanceTimersByTimeAsync(2000);
    await pending;
    expect(mocked.createUploadIntents.mock.calls[0][2]).not.toHaveProperty('device_uuid');
    expect(mocked.completeUpload.mock.calls[0][2]).not.toHaveProperty('device_uuid');
    expect(mocked.getMaterializeStatus.mock.calls[0][3]).not.toHaveProperty('device_uuid');
  });

  it('loadReady 传 deviceUuid 时 listReady params 带 device_uuid(与 stage 并存)', async () => {
    mocked.listReady.mockResolvedValue({ files: [] });
    await botSessionFileService.loadReady('bot-1', 'sess-1', '900003', 'owner-1', undefined, 'online', 'DEVICE-A');
    expect(mocked.listReady).toHaveBeenCalledWith('bot-1', 'sess-1', {
      user_id: '900003',
      owner_id: 'owner-1',
      stage: 'online',
      device_uuid: 'DEVICE-A',
    });
  });

  it('loadReady 不传 deviceUuid 时 params 无该键', async () => {
    mocked.listReady.mockResolvedValue({ files: [] });
    await botSessionFileService.loadReady('bot-1', 'sess-1', '900003', 'owner-1', undefined, 'online');
    expect(mocked.listReady).toHaveBeenCalledWith('bot-1', 'sess-1', {
      user_id: '900003',
      owner_id: 'owner-1',
      stage: 'online',
    });
  });
});

describe('remove/内容链 device_uuid 拼装(openspec add-session-files-device-uuid 扩展段)', () => {
  beforeEach(() => {
    [
      mocked.deleteFile,
      mocked.getContentBlob,
      mocked.buildBotSessionFileContentUrl,
    ].forEach((m) => m?.mockClear?.());
    mocked.deleteFile.mockResolvedValue({ deleted: true });
    mocked.getContentBlob.mockResolvedValue(new Blob(['x']));
    mocked.buildBotSessionFileContentUrl.mockReturnValue('/content?x=1');
  });

  it('remove 传 deviceUuid 时 deleteFile params 含 device_uuid', async () => {
    const res = await botSessionFileService.remove('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'online', 'DEVICE-A');
    expect(res.ok).toBe(true);
    expect(mocked.deleteFile.mock.calls[0][3]).toEqual({
      user_id: '900003',
      owner_id: 'owner-1',
      stage: 'online',
      device_uuid: 'DEVICE-A',
    });
  });

  it('remove 不传 deviceUuid 时 params 无该键', async () => {
    await botSessionFileService.remove('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'online');
    expect(mocked.deleteFile.mock.calls[0][3]).toEqual({
      user_id: '900003',
      owner_id: 'owner-1',
      stage: 'online',
    });
  });

  it('resolveContentUrl 传 deviceUuid 时 URL 构造 params 含 device_uuid', () => {
    botSessionFileService.resolveContentUrl('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'inline', 'online', 'DEVICE-A');
    expect(mocked.buildBotSessionFileContentUrl.mock.calls[0][3]).toEqual(
      expect.objectContaining({ device_uuid: 'DEVICE-A' }),
    );
  });

  it('fetchBlob 传 deviceUuid 时 getContentBlob params 含 device_uuid', async () => {
    await botSessionFileService.fetchBlob('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'inline', 'online', 'DEVICE-A');
    expect(mocked.getContentBlob.mock.calls[0][3]).toEqual(
      expect.objectContaining({ device_uuid: 'DEVICE-A' }),
    );
  });

  it('resolveExternalDownloadUrl 传 deviceUuid 时 URL 构造 params 含 device_uuid', async () => {
    // jsdom 无 Response:手工拼最小响应对象(对齐 collaborationSquareService 测试的 stub 形态)
    const originalFetch = (global as { fetch?: unknown }).fetch;
    (global as { fetch?: unknown }).fetch = () =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ download_url: 'https://oss/dl' }),
      } as Response);
    try {
      await botSessionFileService.resolveExternalDownloadUrl('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'online', 'DEVICE-A');
      expect(mocked.buildBotSessionFileContentUrl.mock.calls[0][3]).toEqual(
        expect.objectContaining({ device_uuid: 'DEVICE-A' }),
      );
    } finally {
      (global as { fetch?: unknown }).fetch = originalFetch;
    }
  });
});

describe('f_user_id 拼装 - 好友 Bot 文件全链携带(2026-11-19 需求追加)', () => {
  describe('上传链', () => {
    beforeEach(() => {
      [
        mocked.createUploadIntents,
        mocked.uploadToUrl,
        mocked.completeUpload,
        mocked.getMaterializeStatus,
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

    it.each(['draft', 'verify', 'online'] as const)('好友上传三接口携带身份和 stage=%s', async (stage) => {
      const pending = botSessionFileService.uploadOne('bot-1', 'new-session', 'human_900003', file('a.pdf'), {
        ownerId: 'owner-1', stage, friendUserId: 'human_900003',
      });
      await jest.advanceTimersByTimeAsync(2000);
      expect((await pending).ok).toBe(true);
      const params = { user_id: '900003', owner_id: 'owner-1', stage, f_user_id: '900003' };
      expect(mocked.createUploadIntents.mock.calls[0][2]).toEqual(params);
      expect(mocked.completeUpload.mock.calls[0][2]).toEqual(params);
      expect(mocked.getMaterializeStatus.mock.calls[0][3]).toEqual(params);
    });

    it.each(['draft', 'verify', 'online'] as const)('无好友身份时上传三接口保留服务 stage=%s', async (stage) => {
      const pending = botSessionFileService.uploadOne('bot-1', 'sess-1', 'human_900003', file('a.pdf'), {
        ownerId: 'owner-1', stage, deviceUuid: 'DEVICE-A',
      });
      await jest.advanceTimersByTimeAsync(2000);
      expect((await pending).ok).toBe(true);
      const params = { user_id: '900003', owner_id: 'owner-1', stage, device_uuid: 'DEVICE-A' };
      expect(mocked.createUploadIntents.mock.calls[0][2]).toEqual(params);
      expect(mocked.completeUpload.mock.calls[0][2]).toEqual(params);
      expect(mocked.getMaterializeStatus.mock.calls[0][3]).toEqual(params);
    });
  });

  describe('删除/内容/下载链', () => {
    beforeEach(() => {
      [mocked.deleteFile, mocked.getContentBlob, mocked.buildBotSessionFileContentUrl].forEach((m) => m?.mockClear?.());
      mocked.deleteFile.mockResolvedValue({ deleted: true });
      mocked.getContentBlob.mockResolvedValue(new Blob(['x']));
      mocked.buildBotSessionFileContentUrl.mockReturnValue('/content?x=1');
    });

    it('remove 传 friendUserId 时 deleteFile params 含 f_user_id', async () => {
      const res = await botSessionFileService.remove(
        'bot-1', 'sess-1', 'sr_1', 'human_900003', 'owner-1', 'online', undefined, 'human_900003',
      );
      expect(res.ok).toBe(true);
      expect(mocked.deleteFile.mock.calls[0][3]).toEqual({
        user_id: '900003',
        owner_id: 'owner-1',
        stage: 'online',
        f_user_id: '900003',
      });
    });

    it('resolveContentUrl 传 friendUserId 时 URL 构造 params 含 f_user_id', () => {
      botSessionFileService.resolveContentUrl(
        'bot-1', 'sess-1', 'sr_1', 'human_900003', 'owner-1', 'inline', 'online', undefined, 'human_900003',
      );
      expect(mocked.buildBotSessionFileContentUrl.mock.calls[0][3]).toEqual(
        expect.objectContaining({ f_user_id: '900003' }),
      );
    });

    it('fetchBlob 传 friendUserId 时 getContentBlob params 含 f_user_id', async () => {
      await botSessionFileService.fetchBlob(
        'bot-1', 'sess-1', 'sr_1', 'human_900003', 'owner-1', 'inline', 'online', undefined, 'human_900003',
      );
      expect(mocked.getContentBlob.mock.calls[0][3]).toEqual(
        expect.objectContaining({ user_id: '900003', f_user_id: '900003' }),
      );
    });

    it('resolveExternalDownloadUrl 传 friendUserId 时 URL 构造 params 含 f_user_id', async () => {
      const originalFetch = (global as { fetch?: unknown }).fetch;
      (global as { fetch?: unknown }).fetch = () =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ download_url: 'https://oss/dl' }),
        } as Response);
      try {
        await botSessionFileService.resolveExternalDownloadUrl(
          'bot-1', 'sess-1', 'sr_1', 'human_900003', 'owner-1', 'online', undefined, 'human_900003',
        );
        expect(mocked.buildBotSessionFileContentUrl.mock.calls[0][3]).toEqual(
          expect.objectContaining({ f_user_id: '900003' }),
        );
      } finally {
        (global as { fetch?: unknown }).fetch = originalFetch;
      }
    });

    it('非好友形态(不传 friendUserId)params 无该键', async () => {
      await botSessionFileService.remove('bot-1', 'sess-1', 'sr_1', '900003', 'owner-1', 'online', undefined);
      expect(mocked.deleteFile.mock.calls[0][3]).not.toHaveProperty('f_user_id');
    });
  });
});
