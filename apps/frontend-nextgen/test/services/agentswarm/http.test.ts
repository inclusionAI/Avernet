import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import { agentswarmRequest, AgentswarmRequestError } from '@/services/agentswarm/http';

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));

const request = backendRequest as jest.Mock;

beforeEach(() => request.mockReset());

test('a successful response is bare JSON, not an envelope', async () => {
  // Verified against the gateway: POST /runs answers {"run_id": ...} directly.
  request.mockResolvedValue({ run_id: 'r1' });
  await expect(agentswarmRequest('/runs', { method: 'POST' })).resolves.toEqual({ run_id: 'r1' });
});

test('a bare list payload is returned as-is', async () => {
  request.mockResolvedValue({ items: [{ run_id: 'r1', status: 'RUNNING' }], total: 1, page: 1, page_size: 20 });
  const result = await agentswarmRequest<{ items: Array<{ run_id: string }> }>('/runs');
  expect(result.items[0].run_id).toBe('r1');
});

test('an enveloped 2xx still decodes to its data', async () => {
  request.mockResolvedValue({ code: 200000, data: { run_id: 'r1' } });
  await expect(agentswarmRequest('/runs/r1')).resolves.toEqual({ run_id: 'r1' });
});

test('every request carries the tenant via params and no transient retry', async () => {
  request.mockResolvedValue({ run_id: 'r1' });
  await agentswarmRequest('/runs/r1', { params: { tenant: 't' } });
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/agentswarm/runs/r1',
    expect.objectContaining({ method: 'GET', params: { tenant: 't' }, retryOnTransient: false }),
  );
});

test('a 400 is classified as a missing identity source and carries the gateway text', async () => {
  request.mockRejectedValue(new BackendRequestError('bad request', { status: 400, apiPath: '/x', data: { error: 'identity source required' } }));
  await expect(agentswarmRequest('/runs', { method: 'POST' })).rejects.toMatchObject({
    kind: 'identity_source_missing',
    status: 400,
    message: 'identity source required',
  });
});

test('a 422 is classified as an unusable identity source', async () => {
  request.mockRejectedValue(new BackendRequestError('unprocessable', { status: 422, apiPath: '/x', data: { error: 'no passport' } }));
  await expect(agentswarmRequest('/runs', { method: 'POST' })).rejects.toBeInstanceOf(AgentswarmRequestError);
  await expect(agentswarmRequest('/runs', { method: 'POST' })).rejects.toMatchObject({ kind: 'identity_source_invalid' });
});

test('a FastAPI validation issue list still yields a readable explanation', async () => {
  request.mockRejectedValue(
    new BackendRequestError('unprocessable', { status: 422, apiPath: '/x', data: { detail: [{ msg: 'field required' }] } }),
  );
  await expect(agentswarmRequest('/runs')).rejects.toMatchObject({ message: 'field required' });
});

test('a 502 is unconfirmed rather than a plain refusal', async () => {
  request.mockRejectedValue(new BackendRequestError('bad gateway', { status: 502, apiPath: '/x' }));
  await expect(agentswarmRequest('/runs', { method: 'POST' })).rejects.toMatchObject({ kind: 'unconfirmed' });
});

test('polling never opts into transient retry so one tick cannot become a burst', async () => {
  request.mockResolvedValue({ code: 200000, data: { run_id: 'r1' } });
  await agentswarmRequest('/runs/r1');
  expect(request).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ retryOnTransient: false }));
});

test('an enveloped success with no data is still not treated as success', async () => {
  request.mockResolvedValue({ code: 200000, data: null });
  await expect(agentswarmRequest('/runs/r1')).rejects.toMatchObject({ kind: 'unknown' });
});

test('an envelope with a non-2xx code is refused even though the transport resolved', async () => {
  request.mockResolvedValue({ code: 404000, data: null });
  await expect(agentswarmRequest('/runs/r1')).rejects.toMatchObject({ kind: 'not_found', status: 404 });
});

test('a 2xx envelope with no data is not treated as success', async () => {
  request.mockResolvedValue({ code: 200000, data: null });
  await expect(agentswarmRequest('/runs/r1')).rejects.toMatchObject({ kind: 'unknown' });
});