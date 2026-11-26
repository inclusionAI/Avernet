import { BackendRequestError } from '@/services/backendApi/httpClient';
import * as httpClient from '@/services/backendApi/httpClient';
import { listExecutableWorkflows, listWorkflows } from '@/services/backendApi/workflows/workflowController';
import { fetchWorkflows } from '@/services/tasks/taskService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';

jest.mock('@/stores/identityStore', () => ({
  useIdentityStore: { getState: () => ({ currentIdentityId: 'authenticated-viewer' }) },
}));

const rows = [
  { workflowId: 'workflow-1', title: 'Executable workflow', packId: null, updatedAt: 1 },
  { workflowId: 'admin-visible-workflow', title: 'Server-authorized workflow', packId: 'pack-1', updatedAt: 2 },
];

function response(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  } as Response;
}

function requestUrl(spy: jest.SpyInstance, call = 0): URL {
  return new URL(String(spy.mock.calls[call][0]), 'http://localhost');
}

afterEach(() => {
  jest.restoreAllMocks();
  useErrorNotifyStore.getState().reset();
});

it('requests execute permission with a bare composite Bot ID through the existing authenticated request path', async () => {
  const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(response(rows));

  await expect(listExecutableWorkflows('real-owner', ' b:real-owner ')).resolves.toEqual(rows);

  const url = requestUrl(spy);
  expect(url.pathname).toBe('/api/workflows');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    permission: 'execute', botOwnerId: 'real-owner', botId: 'b', user_id: 'authenticated-viewer',
  });
  expect(spy).toHaveBeenCalledTimes(1);
  expect(spy).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: 'GET', credentials: 'include' }));
});

it('preserves view-list callers, both scoped and unscoped, without adding execute permission', async () => {
  const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(response(rows));

  await expect(listWorkflows('owner-1', 'bot-main:owner-1')).resolves.toEqual(rows);
  await expect(listWorkflows()).resolves.toEqual(rows);

  expect(Object.fromEntries(requestUrl(spy).searchParams)).toEqual({
    botOwnerId: 'owner-1', botId: 'bot-main', user_id: 'authenticated-viewer',
  });
  expect(Object.fromEntries(requestUrl(spy, 1).searchParams)).toEqual({ user_id: 'authenticated-viewer' });
});

it('propagates HTTP failure without a second view/all request or fixture success', async () => {
  const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(response({ message: 'Workflow service unavailable' }, 503));

  await expect(listExecutableWorkflows('real-owner', 'b')).rejects.toBeInstanceOf(BackendRequestError);

  expect(spy).toHaveBeenCalledTimes(1);
  expect(requestUrl(spy).searchParams.get('permission')).toBe('execute');
});

it.each([
  { owner: undefined, botId: undefined, params: {} },
  { owner: 'owner-1', botId: undefined, params: { botOwnerId: 'owner-1' } },
  { owner: undefined, botId: 'b:owner-1', params: { botId: 'b' } },
])('preserves the optional executable-list API scope ($owner, $botId)', async ({ owner, botId, params }) => {
  const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(response(rows));

  await expect(listExecutableWorkflows(owner, botId)).resolves.toEqual(rows);

  expect(Object.fromEntries(requestUrl(spy).searchParams)).toEqual({
    ...params, permission: 'execute', user_id: 'authenticated-viewer',
  });
  expect(spy).toHaveBeenCalledTimes(1);
});

it('keeps the collaborating requester separate from the composite asset owner through the full service/controller chain', async () => {
  const spy = jest.spyOn(globalThis, 'fetch').mockResolvedValue(response(rows));

  await expect(fetchWorkflows('collaborating-requester', 'b:real-owner')).resolves.toEqual(rows);

  expect(Object.fromEntries(requestUrl(spy).searchParams)).toEqual({
    permission: 'execute', botOwnerId: 'real-owner', botId: 'b', user_id: 'authenticated-viewer',
  });
  expect(spy).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ credentials: 'include' }));
});

describe('workflowController', () => {
  let backendRequest: jest.SpiedFunction<typeof httpClient.backendRequest>;

  beforeEach(() => {
    backendRequest = jest.spyOn(httpClient, 'backendRequest');
  });

  test('keeps the management list on the legacy view query', async () => {
    backendRequest.mockResolvedValue([]);

    await listWorkflows('146836', 'default:146836');

    expect(backendRequest).toHaveBeenCalledWith('/api/workflows', {
      method: 'GET',
      params: { botOwnerId: '146836', botId: 'default' },
    });
  });

  test('requests only executable workflows for the task menu', async () => {
    backendRequest.mockResolvedValue([]);

    await listExecutableWorkflows('146836', 'default:146836');

    expect(backendRequest).toHaveBeenCalledWith('/api/workflows', {
      method: 'GET',
      params: { botOwnerId: '146836', botId: 'default', permission: 'execute' },
    });
  });
});
