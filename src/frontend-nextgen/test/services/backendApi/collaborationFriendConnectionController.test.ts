import {
  acceptFriendConnectionRequest,
  cancelFriendConnectionRequest,
  createFriendConnectionRequest,
  deleteFriendConnection,
  listFriendConnectionRequests,
  listFriendConnections,
  rejectFriendConnectionRequest,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (
  httpClient as unknown as {
    backendRequest: jest.Mock<(...args: any[]) => any>;
  }
).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('collaboration friend connection controller', () => {
  it('creates a Human to Bot connection request from the Gateway Principal', async () => {
    backendRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });
    const signal = new AbortController().signal;

    await createFriendConnectionRequest({ to_actor: { type: 'bot', id: 'bot-1' } }, signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/friend-connections/requests', {
      method: 'POST',
      data: { to_actor: { type: 'bot', id: 'bot-1' } },
      injectUserId: false,
      signal,
    });
  });

  it('lists sent pending requests with explicit actor filters and cancellation', async () => {
    backendRequest.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });
    const signal = new AbortController().signal;

    await listFriendConnectionRequests(
      {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: '327325',
        page: 1,
        page_size: 100,
      },
      signal,
    );

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/friend-connections/requests', {
      method: 'GET',
      params: {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: '327325',
        page: 1,
        page_size: 100,
      },
      injectUserId: false,
      signal,
    });
  });

  it('lists current Human friend connections without injecting user_id', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      data: { items: [{ actor: { type: 'bot', id: 'bot-1' }, is_online: false }], total: 1 },
    });

    await listFriendConnections({ actor_type: 'human', actor_id: '327325' });

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/friend-connections', {
      method: 'GET',
      params: { actor_type: 'human', actor_id: '327325' },
      injectUserId: false,
    });
  });

  it.each([
    ['accept', acceptFriendConnectionRequest, 'accept'],
    ['reject', rejectFriendConnectionRequest, 'reject'],
    ['cancel', cancelFriendConnectionRequest, 'cancel'],
  ] as const)('posts the %s transition without a user_id query', async (_label, action, transition) => {
    backendRequest.mockResolvedValue({ code: 20000, data: {} });

    await action('request-1');

    expect(backendRequest).toHaveBeenCalledWith(
      `/openapi/v1/collaboration/friend-connections/requests/request-1/${transition}`,
      { method: 'POST', injectUserId: false },
    );
  });

  it('deletes the current Human connection with actor filters', async () => {
    backendRequest.mockResolvedValue({ code: 20000, data: {} });

    await deleteFriendConnection({
      from_actor: { type: 'human', id: '327325' },
      to_actor: { type: 'bot', id: 'bot-1' },
    });

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/friend-connections', {
      method: 'DELETE',
      data: {
        from_actor: { type: 'human', id: '327325' },
        to_actor: { type: 'bot', id: 'bot-1' },
      },
      injectUserId: false,
    });
  });
});
