import { callbackCompensationErrorMessage } from '@/domain/taskEscortParity/callbackCompensationErrors';
import { callbackCompensationService as service } from '@/services/taskEscortParity/callbackCompensationService';
import { useIdentityStore } from '@/stores/identityStore';
import {
  callbackCompensationBatch as batch,
  callbackCompensationCapabilities as capabilities,
  callbackCompensationPreview as preview,
} from './callbackCompensationFixtures';

const originalFetch = global.fetch;
const scope = { workflowId: 'wf/a', environment: 'PRE' };
beforeEach(() => {
  useIdentityStore.getState().setCurrentIdentityId('not-an-admin-proof');
});
afterEach(() => {
  global.fetch = originalFetch;
  useIdentityStore.getState().reset();
});
const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

it('passes actual backendRequest cookies, abort signals and mutation JSON CSRF through fetch without identity spoofing or retries', async () => {
  const fetchMock = jest
    .fn()
    .mockResolvedValueOnce(response(capabilities))
    .mockResolvedValueOnce(response(preview))
    .mockResolvedValueOnce(response(batch))
    .mockResolvedValueOnce(response(batch))
    .mockResolvedValueOnce(response({ ...batch, status: 'stopped' }));
  global.fetch = fetchMock;
  const signal = new AbortController().signal;
  await service.capabilities(scope.workflowId, signal);
  await service.preview(scope, { configId: 'cfg', flowIds: ['r1'] }, signal);
  await service.create(
    scope,
    { previewId: 'preview', itemIds: ['eligible'], reason: '人工核实后只补发通知消息', requestKey: 'one-key' },
    signal,
  );
  await service.batch(scope, batch.batchId, signal);
  await service.stop(scope, batch.batchId, signal);
  expect(fetchMock).toHaveBeenCalledTimes(5);
  for (const [url, options] of fetchMock.mock.calls) {
    expect(url).toMatch(/^\/openapi\/v1\/harnessflow\/api\/workflows\/wf%2Fa\/callback-compensations(?:\/|$)/);
    expect(url).not.toContain('user_id');
    expect(options).toEqual(expect.objectContaining({ credentials: 'include', signal }));
    expect(options.headers).not.toHaveProperty('userId');
    expect(options.headers).not.toHaveProperty('Authorization');
  }
  for (const index of [1, 2, 4]) {
    const options = fetchMock.mock.calls[index][1];
    expect(options.headers).toEqual({ 'Content-Type': 'application/json', 'X-Callback-Compensation-CSRF': '1' });
    expect(() => JSON.parse(options.body)).not.toThrow();
  }
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ configId: 'cfg', flowIds: ['r1'] });
  expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({});
});

it('retains service codes for the safe mapper, never automatically retries a failed create', async () => {
  const fetchMock = jest.fn().mockResolvedValue(response({ code: 'EXECUTION_DISABLED', message: 'secret=never' }, 409));
  global.fetch = fetchMock;
  const failed = service.create(scope, {
    previewId: 'preview',
    itemIds: ['eligible'],
    reason: '人工核实后只补发通知消息',
    requestKey: 'one-key',
  });
  await expect(failed).rejects.toMatchObject({ status: 409, data: { code: 'EXECUTION_DISABLED' } });
  expect(callbackCompensationErrorMessage(await failed.catch((error) => error), '安全提示')).toBe(
    '补偿执行尚未启用，当前只能预览。',
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
