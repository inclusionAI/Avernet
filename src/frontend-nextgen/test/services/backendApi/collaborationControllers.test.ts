import * as chatController from '@/services/backendApi/chat/chatMessageController';
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as invitationController from '@/services/backendApi/collaboration/collaborationInvitationController';
import {
  discoverPublicBots,
  PublicBotCatalogError,
  searchPublicBots,
} from '@/services/backendApi/collaboration/publicBotController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
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

describe('collaboration group controller', () => {
  it('listGroups passes view_bot_id and q and strategy', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      message: '',
      data: { items: [], total: 0, offset: 0, limit: 20 },
      request_id: 'r',
    });
    await groupController.listGroups({ view_bot_id: 'bot-1', q: 'abc', strategy: 'chat', offset: 0, limit: 20 });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups', {
      method: 'GET',
      params: { view_bot_id: 'bot-1', q: 'abc', strategy: 'chat', offset: 0, limit: 20 },
    });
  });

  it('listPublicGroups fixes public visibility and normal kind without injecting user_id', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      message: '',
      data: { items: [], total: 0, offset: 0, limit: 20 },
      request_id: 'r',
    });
    const signal = new AbortController().signal;
    await groupController.listPublicGroups({ q: 'agent', offset: 20, limit: 20 }, signal);
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups', {
      method: 'GET',
      params: { visibility: 'public', kind: 'normal', q: 'agent', offset: 20, limit: 20 },
      injectUserId: false,
      signal,
    });
  });

  it('rejects ACE and malformed public group list responses', async () => {
    backendRequest.mockResolvedValueOnce({
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      decisionBy: 'ACE',
    });
    await expect(groupController.listPublicGroups()).rejects.toMatchObject({ code: 'unauthenticated' });

    backendRequest.mockResolvedValueOnce({ code: 20000, data: { items: 'invalid', total: 0 } });
    await expect(groupController.listPublicGroups()).rejects.toMatchObject({ code: 'protocol_error' });
  });

  it('createGroup posts chat strategy with driver_bot_uuid', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: {}, request_id: 'r' });
    await groupController.createGroup({
      group_kind: 'normal',
      name: 'n',
      collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
      driver_bot_uuid: 'bot-1',
      participants: [{ actor_id: 'bot-1', role: 'driver' }],
    });
    const [, opts] = backendRequest.mock.calls[0];
    expect(backendRequest.mock.calls[0][0]).toBe('/openapi/v1/collaboration/groups');
    expect(opts.method).toBe('POST');
    expect(opts.data).toEqual(expect.objectContaining({ group_kind: 'normal', name: 'n' }));
  });

  it('updateGroup PATCHes name/visibility/delivery_policy subset', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: {}, request_id: 'r' });
    await groupController.updateGroup('g1', { visibility: 'public' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups/g1', {
      method: 'PATCH',
      data: { visibility: 'public' },
    });
  });

  it('deleteGroup hits DELETE', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { deleted: true }, request_id: 'r' });
    await groupController.deleteGroup('g1');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups/g1', { method: 'DELETE' });
  });

  it('addGroupParticipant posts actor_id to group participants', async () => {
    backendRequest.mockResolvedValue({ code: 20100, message: '', data: {}, request_id: 'r' });
    await groupController.addGroupParticipant('g1', 'bot-1');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups/g1/participants', {
      method: 'POST',
      data: { actor_id: 'bot-1' },
    });
  });

  it('deleteGroupParticipant deletes actor from group participants', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: {}, request_id: 'r' });
    await groupController.deleteGroupParticipant('g1', 'bot-1');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups/g1/participants/bot-1', {
      method: 'DELETE',
    });
  });
});

describe('collaboration session controller', () => {
  it('listSessionMessages takes before/limit', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      message: '',
      data: { messages: [], next_cursor: null, has_more: false },
      request_id: 'r',
    });
    await sessionController.listSessionMessages('s1', { before: 'abc', limit: 50 });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/messages', {
      method: 'GET',
      params: { before: 'abc', limit: 50 },
    });
  });

  it('createSessionToken POSTs to token endpoint', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      message: '',
      data: { token: 't', expires_at: 123 },
      request_id: 'r',
    });
    const res = await sessionController.createSessionToken('s1');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/token', { method: 'POST' });
    expect(res.data!.token).toBe('t');
  });

  it('collectSession POSTs to session collect endpoint', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { collected: true }, request_id: 'r' });
    await sessionController.collectSession('s1', { participant: 'bot-1' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/collect', {
      method: 'POST',
      data: { participant: 'bot-1' },
    });
  });

  it('uncollectSession DELETEs session collect endpoint', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { collected: false }, request_id: 'r' });
    await sessionController.uncollectSession('s1', { participant: 'bot-1' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/collect', {
      method: 'DELETE',
      params: { participant: 'bot-1' },
    });
  });

  it('updateSessionMemberMode PATCHes mode', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: {}, request_id: 'r' });
    await sessionController.updateSessionMemberMode('s1', 'bot-x', { mode: 'muted' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/participants/bot-x', {
      method: 'PATCH',
      data: { mode: 'muted' },
    });
  });

  it('addSessionParticipant posts bot_uuid to session participants', async () => {
    backendRequest.mockResolvedValue({ code: 20100, message: '', data: {}, request_id: 'r' });
    await sessionController.addSessionParticipant('s1', 'bot-x');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/participants', {
      method: 'POST',
      data: { bot_uuid: 'bot-x' },
    });
  });

  it('deleteSessionParticipant deletes bot from session participants', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: {}, request_id: 'r' });
    await sessionController.deleteSessionParticipant('s1', 'bot-x');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/participants/bot-x', {
      method: 'DELETE',
    });
  });
});

describe('invitation controller', () => {
  it('acceptInvitation POSTs to accept endpoint, meeting 410 on expired idempotent semantics via 4xx path', async () => {
    backendRequest.mockResolvedValue({
      code: 20000,
      message: '',
      data: { target_type: 'group', target_id: 'g1', joined: true, already_joined: false },
      request_id: 'r',
    });
    await invitationController.acceptInvitation('tk');
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/invitations/tk/accept', {
      method: 'POST',
      data: {},
    });
  });

  it('createGroupInvitation posts expires_in_seconds', async () => {
    backendRequest.mockResolvedValue({
      code: 20100,
      message: '',
      data: { token: 't2', target_type: 'group', target_id: 'g1', state: 'pending', created_at: 1 },
      request_id: 'r',
    });
    await invitationController.createGroupInvitation('g1', { expires_in_seconds: 3600 });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/groups/g1/invitations', {
      method: 'POST',
      data: { expires_in_seconds: 3600 },
    });
  });

  it('createSessionInvitation posts expires_in_seconds to session invitations', async () => {
    backendRequest.mockResolvedValue({
      code: 20100,
      message: '',
      data: { token: 't3', target_type: 'session', target_id: 's1', state: 'pending', created_at: 1 },
      request_id: 'r',
    });
    await invitationController.createSessionInvitation('s1', { expires_in_seconds: 3600 });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/sessions/s1/invitations', {
      method: 'POST',
      data: { expires_in_seconds: 3600 },
    });
  });
});

describe('chat message controller', () => {
  it('sendMessage posts to /openapi/v1/chat/messages with bot_id and message', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { message_id: 'm1' }, request_id: 'r' });
    await chatController.sendMessage({ bot_id: 'bot-1', message: 'hi' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/chat/messages', {
      method: 'POST',
      data: { bot_id: 'bot-1', message: 'hi' },
    });
  });

  it('sendStreamMessage uses stream endpoint', async () => {
    backendRequest.mockResolvedValue({});
    await chatController.sendStreamMessage({ bot_id: 'bot-1', message: 'hi' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/chat/messages/stream', {
      method: 'POST',
      data: { bot_id: 'bot-1', message: 'hi' },
    });
  });
});

describe('managed collaboration bot controller', () => {
  it('lists managed Bot rows with Swagger filters, Human Principal auth and cancellation', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { items: [] }, request_id: 'r' });
    const signal = new AbortController().signal;

    await botController.listMyBots({ kind: 'bot', status: 'online', offset: 0, limit: 20 }, signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/mine', {
      method: 'GET',
      params: { kind: 'bot', status: 'online', offset: 0, limit: 20 },
      injectUserId: false,
      signal,
    });
  });

  it('loads one managed Bot with Human Principal auth and cancellation', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { bot_id: 'bot-1' }, request_id: 'r' });
    const signal = new AbortController().signal;

    await botController.getCollaborationBot('bot-1', signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/bot-1', {
      method: 'GET',
      injectUserId: false,
      signal,
    });
  });

  it('patches only Swagger-confirmed mutable fields with Human Principal auth and cancellation', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { bot_id: 'bot-1' }, request_id: 'r' });
    const signal = new AbortController().signal;

    await botController.patchCollaborationBot(
      'bot-1',
      {
        visibility: 'public',
        status: 'online',
        descriptor: { summary: 'updated' },
      },
      signal,
    );

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/bot-1', {
      method: 'PATCH',
      data: {
        visibility: 'public',
        status: 'online',
        descriptor: { summary: 'updated' },
      },
      injectUserId: false,
      signal,
    });
  });

  it('lists Human actor friendships without injecting a second user_id', async () => {
    backendRequest.mockResolvedValue({ code: 20000, data: { items: [], total: 0 }, request_id: 'r' });
    const signal = new AbortController().signal;

    await botController.listBotFriendships('human_327325', { offset: 0, limit: 100 }, signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/human_327325/friendships', {
      method: 'GET',
      params: { offset: 0, limit: 100 },
      injectUserId: false,
      signal,
    });
  });

  it('creates a Human to Bot friend request with the target Bot in the body', async () => {
    backendRequest.mockResolvedValue({ code: 20100, data: { request_id: 'r1', state: 'pending' }, request_id: 'r' });
    const signal = new AbortController().signal;

    await botController.createBotFriendRequest('human_327325', { to_bot_uuid: 'bot-1' }, signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/human_327325/friend-requests', {
      method: 'POST',
      data: { to_bot_uuid: 'bot-1' },
      injectUserId: false,
      signal,
    });
  });
});

describe('public bot catalog controller', () => {
  it('searchPublicBots uses the bot-catalog endpoint and preserves Swagger query params', async () => {
    backendRequest.mockResolvedValue({
      code: 200000,
      message: '',
      data: { items: [], total: 0 },
      request_id: 'r',
    });
    const signal = new AbortController().signal;
    await searchPublicBots({ search: 'workflow', page: 2, page_size: 20 }, signal);
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/catalog/search', {
      method: 'GET',
      params: { search: 'workflow', page: 2, page_size: 20 },
      injectUserId: false,
      signal,
    });
  });

  it('rejects an HTTP 200 ACE login response as unauthenticated', async () => {
    backendRequest.mockResolvedValue({
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      decisionBy: 'ACE',
    });

    await expect(searchPublicBots()).rejects.toEqual(
      expect.objectContaining({ code: 'unauthenticated' } satisfies Partial<PublicBotCatalogError>),
    );
  });

  it('rejects a non-Catalog business success code as a protocol error', async () => {
    backendRequest.mockResolvedValue({ code: 20000, message: '', data: { items: [], total: 0 } });

    await expect(searchPublicBots()).rejects.toEqual(
      expect.objectContaining({ code: 'protocol_error' } satisfies Partial<PublicBotCatalogError>),
    );
  });

  it('uses the confirmed discovery query and a separate endpoint from ordinary catalog search', async () => {
    backendRequest.mockResolvedValue({
      code: 200000,
      message: '',
      data: { items: [], total: 0 },
      request_id: 'r',
    });
    const signal = new AbortController().signal;
    await discoverPublicBots(
      {
        keyword: '能处理会议纪要',
        top_k: 20,
        min_score: 0.1,
        runtime_state: 'online',
      },
      signal,
    );
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/catalog/discover', {
      method: 'GET',
      params: {
        keyword: '能处理会议纪要',
        top_k: 20,
        min_score: 0.1,
        runtime_state: 'online',
      },
      injectUserId: false,
      signal,
    });
  });

  it('applies the Catalog envelope policy to discovery responses', async () => {
    backendRequest.mockResolvedValueOnce({
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      decisionBy: 'ACE',
    });
    await expect(discoverPublicBots({ keyword: '规划' })).rejects.toMatchObject({ code: 'unauthenticated' });

    backendRequest.mockResolvedValueOnce({ code: 20000, data: { items: [], total: 0 } });
    await expect(discoverPublicBots({ keyword: '规划' })).rejects.toMatchObject({ code: 'protocol_error' });
  });

  it('keeps an empty catalog response as a successful empty page', async () => {
    const response = {
      code: 200000,
      message: '',
      data: { items: [], total: 0 },
      request_id: 'r',
    };
    backendRequest.mockResolvedValue(response);
    await expect(searchPublicBots()).resolves.toEqual(response);
  });
});
