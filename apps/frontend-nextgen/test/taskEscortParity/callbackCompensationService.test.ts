import { backendRequest } from '@/services/backendApi/httpClient';
import { callbackCompensationService as service } from '@/services/taskEscortParity/callbackCompensationService';
import {
  callbackCompensationBatch as batch,
  callbackCompensationCapabilities as capabilities,
  callbackCompensationPreview as preview,
} from './callbackCompensationFixtures';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const base = '/openapi/v1/harnessflow/api/workflows/wf%2Fa/callback-compensations';
const scope = { workflowId: 'wf/a', environment: 'PRE' };
const input = { configId: 'cfg', from: '2026-11-27T00:00:00.000Z', to: '2026-11-28T00:00:00.000Z' };
beforeEach(() => request.mockReset());

it('uses only fixed workflow endpoints, JSON CSRF for all writes, cookie transport and no user/header/URL override', async () => {
  const signal = new AbortController().signal;
  request.mockResolvedValueOnce(capabilities);
  await service.capabilities('wf/a', signal);
  expect(request).toHaveBeenLastCalledWith(
    `${base}/capabilities`,
    expect.objectContaining({ method: 'GET', signal, injectUserId: false }),
  );
  request.mockResolvedValueOnce(preview);
  await service.preview(
    scope,
    { ...input, url: 'https://untrusted.invalid', headers: { Authorization: 'secret' } } as typeof input,
    signal,
  );
  expect(request).toHaveBeenLastCalledWith(
    `${base}/preview`,
    expect.objectContaining({ method: 'POST', data: input, signal }),
  );
  const create = {
    previewId: 'preview',
    itemIds: ['eligible'],
    reason: '人工核实后只补发通知消息',
    requestKey: 'stable-key',
  };
  request.mockResolvedValueOnce(batch);
  await service.create(scope, create, signal);
  expect(request).toHaveBeenLastCalledWith(base, expect.objectContaining({ method: 'POST', data: create }));
  request.mockResolvedValueOnce(batch);
  await service.batch(scope, 'batch/a', signal);
  expect(request).toHaveBeenLastCalledWith(`${base}/batch%2Fa`, expect.objectContaining({ method: 'GET' }));
  request.mockResolvedValueOnce({ ...batch, status: 'stopped' });
  await service.stop(scope, 'batch/a', signal);
  expect(request).toHaveBeenLastCalledWith(
    `${base}/batch%2Fa/stop`,
    expect.objectContaining({ method: 'POST', data: {} }),
  );
  for (const [, options] of request.mock.calls) {
    expect(options.injectUserId).toBe(false);
    expect(options.retryOnTransient).toBe(false);
    expect(options.suppressErrorNotification).toBe(true);
    expect(options.params).toBeUndefined();
    const expectedHeaders =
      options.method === 'POST'
        ? { 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' }
        : undefined;
    expect(options.headers).toEqual(expectedHeaders);
  }
});

it('retains only the sanitized capabilities config contract, never fetches old configs', async () => {
  request.mockResolvedValueOnce({
    ...capabilities,
    secret: 'never',
    configs: [{ ...capabilities.configs[0], secret: 'never', headers: { auth: 'never' }, url: 'never' }],
  });
  expect(await service.capabilities('wf/a')).toEqual(capabilities);
  expect(JSON.stringify(await Promise.resolve(request.mock.calls))).not.toContain('/callback-configs');
});

it('rejects cross-workflow/environment/batch/receiver responses and malformed states', async () => {
  for (const bad of [
    { ...capabilities, workflowId: 'other' },
    { ...capabilities, canPreview: false },
    { ...capabilities, executionEnabled: 'true' },
  ]) {
    request.mockResolvedValueOnce(bad);
    await expect(service.capabilities('wf/a')).rejects.toThrow();
  }
  for (const bad of [
    { ...preview, environment: 'PROD' },
    { ...preview, receiver: { ...preview.receiver, configId: 'other' } },
    { ...preview, items: [{ ...preview.items[0], status: 'invented' }] },
  ]) {
    request.mockResolvedValueOnce(bad);
    await expect(service.preview(scope, input)).rejects.toThrow();
  }
  for (const bad of [
    { ...batch, workflowId: 'other' },
    { ...batch, environment: 'PROD' },
    { ...batch, batchId: 'other' },
  ]) {
    request.mockResolvedValueOnce(bad);
    await expect(service.batch(scope, 'batch/a')).rejects.toThrow();
  }
});

it('does not convert denied/unavailable or transport failures into demo responses', async () => {
  for (const status of [403, 503]) {
    const error = Object.assign(new Error('sensitive server detail'), { status });
    request.mockRejectedValueOnce(error);
    await expect(service.capabilities('wf/a')).rejects.toBe(error);
  }
});

it('sends flowIds-only previews with no from/to fields, and keeps invalid-target configs previewable without rendering arbitrary URLs', async () => {
  request.mockResolvedValueOnce(preview);
  await service.preview(scope, { configId: 'cfg', flowIds: ['run-eligible'] });
  expect(request).toHaveBeenLastCalledWith(
    `${base}/preview`,
    expect.objectContaining({ data: { configId: 'cfg', flowIds: ['run-eligible'] } }),
  );
  request.mockResolvedValueOnce({
    ...capabilities,
    configs: [{ ...capabilities.configs[0], targetOrigin: 'invalid' }],
  });
  const result = await service.capabilities('wf/a');
  expect(result.configs).toHaveLength(1);
  expect(result.configs[0].targetOrigin).toBe('目标 Origin 不可用');
});
