import * as http from '@/services/backendApi/httpClient';
import {
  deleteBrowseSubscription,
  listBrowseSubscriptions,
  upsertBrowseSubscription,
} from '@/services/backendApi/lab/botConfigController';

jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

// botConfig 走内部 /api Unified 面（contract §3；预发 teamclawgw-pre 网关未合并 /openapi/v1/bbs 转发 → dev 直连 engine）。
// backendRequest 被 mock，不会自动注入 user_id，故断言只关注 controller 显式下发的 path/参数。

describe('botConfigController (内部 /api Unified 面 §3)', () => {
  beforeEach(() => {
    mockedRequest.mockReset().mockResolvedValue({ code: 200000, data: null });
  });

  it('list 路径 /api/v1/bbs/browse-subscriptions 且 owner_user_id 走 query', async () => {
    await listBrowseSubscriptions('u1');
    expect(mockedRequest).toHaveBeenCalledWith('/api/v1/bbs/browse-subscriptions', {
      method: 'GET',
      params: { owner_user_id: 'u1', page: 1, page_size: 100 },
      signal: undefined,
    });
  });

  it('list 自定义分页参数透传', async () => {
    await listBrowseSubscriptions('u2', undefined as unknown as AbortSignal, 3, 25);
    expect(mockedRequest).toHaveBeenCalledWith(
      '/api/v1/bbs/browse-subscriptions',
      expect.objectContaining({ method: 'GET', params: { owner_user_id: 'u2', page: 3, page_size: 25 } }),
    );
  });

  it('upsert 路径 /api/v1/bots/{id}/bbs/browse-subscription，owner_user_id 走 query（必填），body 只 {note}', async () => {
    await upsertBrowseSubscription({ botId: 'b1', ownerUserId: 'u1', note: '每周扫一遍' });
    expect(mockedRequest).toHaveBeenCalledWith('/api/v1/bots/b1/bbs/browse-subscription', {
      method: 'POST',
      params: { owner_user_id: 'u1' },
      data: { note: '每周扫一遍' },
      signal: undefined,
    });
  });

  it('upsert note 透传 controller（归一在上层 Service；input.note 为空串时直接透传，由后端兜底）', async () => {
    await upsertBrowseSubscription({ botId: 'b1', ownerUserId: 'u1', note: '' });
    expect(mockedRequest).toHaveBeenCalledWith(
      '/api/v1/bots/b1/bbs/browse-subscription',
      expect.objectContaining({ method: 'POST', params: { owner_user_id: 'u1' }, data: { note: '' } }),
    );
  });
  it('upsert note 为 null 时入 body 为 null', async () => {
    await upsertBrowseSubscription({ botId: 'b1', ownerUserId: 'u1', note: null });
    expect(mockedRequest).toHaveBeenCalledWith(
      '/api/v1/bots/b1/bbs/browse-subscription',
      expect.objectContaining({ method: 'POST', params: { owner_user_id: 'u1' }, data: { note: null } }),
    );
  });

  it('delete 路径 /api/v1/bots/{id}/bbs/browse-subscription 且无 owner query（§3.2：仅 bot_id 路径）', async () => {
    await deleteBrowseSubscription('b1');
    const call = mockedRequest.mock.calls[0];
    expect(call[0]).toBe('/api/v1/bots/b1/bbs/browse-subscription');
    expect(call[1]).toEqual({ method: 'DELETE', signal: undefined });
    expect(call[1]).not.toHaveProperty('params');
  });
});
