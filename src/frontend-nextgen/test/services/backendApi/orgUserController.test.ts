import * as httpClient from '@/services/backendApi/httpClient';
import { getOrgUser } from '@/services/backendApi/org/orgUserController';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<(...args: any[]) => any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
  backendRequest.mockResolvedValue({ code: 200000, message: 'OK', data: null, request_id: 'trace' });
});

describe('/openapi/v1/org/user controller', () => {
  it('passes the required employee number as an explicit user_id query parameter', async () => {
    await getOrgUser('447147');

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/org/user', {
      method: 'GET',
      params: { user_id: '447147' },
      injectUserId: false,
      signal: undefined,
    });
  });

  it('forwards the abort signal without falling back to implicit identity injection', async () => {
    const signal = new AbortController().signal;

    await getOrgUser('447147', signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/org/user', {
      method: 'GET',
      params: { user_id: '447147' },
      injectUserId: false,
      signal,
    });
  });
});
