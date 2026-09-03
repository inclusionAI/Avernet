import { getWorkerConfig, updateWorkerConfig } from '@/services/backendApi/bcsfuse/bcsfuseController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const response = (data: unknown = null) =>
  Promise.resolve({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  } as Response);

const failingResponse = (status: number, data: unknown = {}) =>
  Promise.resolve({
    ok: false,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  } as Response);

describe('bcsfuse worker config controller', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('gets the fusion config with the composite worker id and forwards cancellation', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() =>
      response({
        success: true,
        worker_id: '20260720_4kekavkp:447147',
        fusion_enable: true,
        version: 10,
        server_ip: '11.38.221.87',
      }),
    );
    const signal = new AbortController().signal;

    await expect(getWorkerConfig('20260720_4kekavkp:447147', signal)).resolves.toEqual({
      success: true,
      worker_id: '20260720_4kekavkp:447147',
      fusion_enable: true,
      version: 10,
      server_ip: '11.38.221.87',
    });
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bcsfuse/workers/20260720_4kekavkp:447147/config',
      expect.objectContaining({ method: 'GET', signal }),
    );
  });

  it('unwraps the unified API envelope returned by the pre environment', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() =>
      response({
        code: 200000,
        message: 'OK',
        data: { success: true, worker_id: 'bot-1', fusion_enable: true, version: 3 },
      }),
    );

    await expect(getWorkerConfig('bot-1')).resolves.toEqual({
      success: true,
      worker_id: 'bot-1',
      fusion_enable: true,
      version: 3,
    });
    expect(spy).toHaveBeenCalledWith('/openapi/v1/bcsfuse/workers/bot-1/config', expect.anything());
  });

  it('unwraps the unified API envelope after updating the fusion setting', async () => {
    jest.spyOn(globalThis, 'fetch').mockImplementation(() =>
      response({
        code: 200000,
        message: 'OK',
        data: { success: true, worker_id: 'bot-1', fusion_enable: false, version: 4 },
      }),
    );

    await expect(updateWorkerConfig('bot-1', { fusion_enable: false })).resolves.toMatchObject({
      worker_id: 'bot-1',
      fusion_enable: false,
      version: 4,
    });
  });

  it('updates fusion_enable through the worker config endpoint', async () => {
    const spy = jest
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() =>
        response({ success: true, worker_id: '20260825_mbu0ey8f:447147', fusion_enable: false, version: 2 }),
      );
    const signal = new AbortController().signal;

    await expect(
      updateWorkerConfig('20260825_mbu0ey8f:447147', { fusion_enable: false }, signal),
    ).resolves.toMatchObject({ fusion_enable: false });
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bcsfuse/workers/20260825_mbu0ey8f:447147/config',
      expect.objectContaining({
        method: 'PUT',
        signal,
        body: JSON.stringify({ fusion_enable: false }),
      }),
    );
  });
});

describe('getWorkerConfig — 404 抑制协议层默认 toast(无配置 ≠ 用户可见异常)', () => {
  // 协作权限查询接口 GET .../workers/{id}/config 返回 404 语义为「该 Worker 尚未配置画像公开」,
  // 属可降级态:调用方(collaborationPrivacyRuntimeAdapter → profilePublicStatus 'unavailable' 置灰开关;
  // bcsfuseService → fusionEnable false)已据此降级。协议层 httpClient 对任意非 2xx 已 enqueue 默认 toast,
  // controller 须就 status===404 显式 cancel 该 toastKey,使开关置灰同时不弹异常;其余状态码维持默认提示。
  beforeEach(() => {
    useErrorNotifyStore.getState().reset();
  });
  afterEach(() => {
    jest.restoreAllMocks();
    useErrorNotifyStore.getState().reset();
  });

  it('404 → 仍抛 BackendRequestError 供降级,但默认提示 toast 被 cancel(开关置灰不弹异常)', async () => {
    jest.spyOn(globalThis, 'fetch').mockImplementation(() => failingResponse(404));

    await expect(getWorkerConfig('bot-1')).rejects.toBeInstanceOf(BackendRequestError);

    const items = useErrorNotifyStore.getState().drain();
    expect(items).toHaveLength(1);
    expect(items[0].cancelled).toBe(true);
  });

  it('非 404(500) → 不抑制默认提示(维持现状),圈定仅 404 静默语义', async () => {
    jest.spyOn(globalThis, 'fetch').mockImplementation(() => failingResponse(500));

    await expect(getWorkerConfig('bot-1')).rejects.toBeInstanceOf(BackendRequestError);

    const items = useErrorNotifyStore.getState().drain();
    expect(items).toHaveLength(1);
    expect(items[0].cancelled).toBeFalsy();
  });
});
