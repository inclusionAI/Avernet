import { getBotChat, listBotChats } from '@/services/backendApi/bots/botChatController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('botChatController', () => {
  it('通过 bot scope 列出日志并显式透传 user_id', async () => {
    backendRequest.mockResolvedValue({ code: 200000, message: 'OK', data: { sessions: [] } } as never);
    await listBotChats('bot/a', { user_id: 'user-demo', owner_id: 'owner', session_key: 'agent:main' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/bot%2Fa/chats', {
      method: 'GET',
      params: { user_id: 'user-demo', owner_id: 'owner', session_key: 'agent:main' },
    });
  });

  it('详情路径编码 trace_id 并保留身份参数', async () => {
    backendRequest.mockResolvedValue({ code: 200000, message: 'OK', data: { id: 'trace/1' } } as never);
    await getBotChat('bot-1', 'trace/1', { user_id: 'user-demo' });
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/bot-1/chats/trace%2F1', {
      method: 'GET',
      params: { user_id: 'user-demo' },
    });
  });
});
