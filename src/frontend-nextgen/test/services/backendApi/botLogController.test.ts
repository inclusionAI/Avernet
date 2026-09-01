import { getBotTrace, getGroupBotTrace, listGroupBotTraces } from '@/services/backendApi/bots/botLogController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('botLogController', () => {
  it('发送 Group path 和查看 Bot 上下文', async () => {
    await listGroupBotTraces('group-a', {
      bot_id: 'viewer',
      user_id: 'collaborator',
      owner_id: 'owner',
      page: 2,
      limit: 100,
    });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/viewer/chats', {
      method: 'GET',
      params: {
        bot_id: 'viewer',
        user_id: 'collaborator',
        owner_id: 'owner',
        page: 2,
        limit: 100,
        group_id: 'group-a',
        match_mode: 'exact',
        time_scope: 'all',
      },
    });
  });

  it('发送 Group Trace 详情上下文', async () => {
    await getGroupBotTrace('trace-a', {
      bot_id: 'viewer',
      group_id: 'group-a',
      user_id: 'collaborator',
      owner_id: 'owner',
    });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/viewer/chats/trace-a', {
      method: 'GET',
      params: { user_id: 'collaborator', owner_id: 'owner' },
    });
  });

  it('保留原 getBotTrace 的无参数调用契约', async () => {
    await getBotTrace('trace-legacy', { bot_id: 'legacy-bot' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/legacy-bot/chats/trace-legacy', {
      method: 'GET',
      params: { user_id: undefined, owner_id: undefined },
    });
  });
});
