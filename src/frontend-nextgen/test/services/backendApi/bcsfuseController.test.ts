import { getWorkerConfig, updateWorkerConfig } from '@/services/backendApi/bcsfuse/bcsfuseController';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

const response = (data: unknown = null) =>
  Promise.resolve({
    ok: true,
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
