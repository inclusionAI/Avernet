// @jest/globals 必须先于被测模块导入：jest.mock 工厂在执行时会引用这里的 jest 绑定，
// 若被测模块先加载会触发工厂求值，此时 jest 绑定尚未初始化（undefined.jest 报错）。
import {
  createBotSession,
  deleteBotSession,
  getBotConnection,
  getBotSession,
  listBotSessionMessages,
  listBotSessions,
} from '@/services/backendApi/bots/privateBotSessionController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<(...args: any[]) => any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('privateBotSessionController', () => {
  it('listBotSessions 拼装 path 与 query(不 encode 路径中的冒号)', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: { items: [], total: 0 }, message: 'OK', request_id: 'r' });
    await listBotSessions('bot-1:2088', { user_id: 'u1', owner_id: '2088', page: 1, page_size: 50 });
    const [url, opts] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot-1:2088/sessions');
    expect(opts).toEqual({ method: 'GET', params: { user_id: 'u1', owner_id: '2088', page: 1, page_size: 50 } });
  });

  it('createBotSession 走 POST 与 body', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: { session_id: 's1' }, message: 'OK', request_id: 'r' });
    const signal = new AbortController().signal;
    await createBotSession('bot-1', { user_id: 'u1', owner_id: '2088' }, { title: 't' }, signal);
    const [url, opts] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot-1/sessions');
    expect(opts.method).toBe('POST');
    expect(opts.data).toEqual({ title: 't' });
    expect(opts.params).toEqual({ user_id: 'u1', owner_id: '2088' });
    expect(opts.injectUserId).toBe(false);
    expect(opts.signal).toBe(signal);
  });

  it('getBotSession 走 GET 并携带 session_id path 段与 user_id query', async () => {
    backendRequest.mockResolvedValue({
      code: 200000,
      data: { session_id: 's1', model: 'openai/gpt-5.3' },
      message: 'OK',
      request_id: 'r',
    });
    await getBotSession('bot-1', 's1', { user_id: 'u1', owner_id: '2088' });
    const [url, opts] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot-1/sessions/s1');
    expect(opts.method).toBe('GET');
    expect(opts.params).toEqual({ user_id: 'u1', owner_id: '2088' });
  });

  it('deleteBotSession 走 DELETE 并带上 session_id path 段', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: { deleted: true }, message: 'OK', request_id: 'r' });
    await deleteBotSession('bot-1', 'sid-9', { user_id: 'u1' });
    const [url, opts] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot-1/sessions/sid-9');
    expect(opts.method).toBe('DELETE');
  });

  it('listBotSessionMessages 路径含 bot_id 与 session_id', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: { items: [], total: 0 }, message: 'OK', request_id: 'r' });
    await listBotSessionMessages('bot-1', 'sid-9', { user_id: 'u1', page: 1, page_size: 50 });
    const [url, opts] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot-1/sessions/sid-9/messages');
    expect(opts.params.page).toBe(1);
  });

  it('getBotConnection 返回原样 envelope(供上层取 sockets[].url)', async () => {
    const envelope = {
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws' }] },
      message: 'OK',
      request_id: 'r',
    };
    backendRequest.mockResolvedValue(envelope);
    const r = await getBotConnection('bot-1', { user_id: 'u1' });
    expect(r.data?.sockets[0].url).toBe('wss://gw/ws');
  });
});
