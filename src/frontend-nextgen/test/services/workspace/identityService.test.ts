/** @jest-environment jsdom */
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import type { DomainError } from '@/services/workspace/identityService';
import { identityService, type LoadIdentitiesResult } from '@/services/workspace/identityService';
import { describe, expect, it, jest } from '@jest/globals';

// identityService 经 testUser→supportProvider transitive 加载 `@tc-chat/adapters`(node_modules 内 ESM),
// jest 直接 load 会报 SyntaxError,此处 stub 掉(只满足模块解析,不被业务逻辑引用)。
jest.mock('@tc-chat/adapters', () => ({}));
// 使用 auto-mock（不带 factory），避免在 hoisted factory 内引用 jest.fn() —— 与 @jest/globals 一起会触发 TDZ。
// auto-mock 会把 listMyBots 替成 jest.fn()，下方强取即可。
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
type SuccessResult = { ok: true; data: LoadIdentitiesResult };
type FailureResult = { ok: false; error: DomainError };
const listMyBots = (botController as unknown as { listMyBots: jest.Mock<any> }).listMyBots;

describe('identityService.loadIdentities', () => {
  it('merges a user identity with mine bots, defaulting active to first bot when no localStorage', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b1', name: 'Bot一号', avatar_url: 'u1', status: 'online' },
          { kind: 'bot', bot_id: 'b2', name: 'Bot二号', avatar_url: 'u2', status: 'offline' },
        ],
        total: 2,
        offset: 0,
        limit: 20,
      },
    });
    const res = await identityService.loadIdentities();
    expect(res.ok).toBe(true);
    const data = (res as SuccessResult).data;
    expect(data.identities.length).toBe(3);
    expect(data.identities[0]).toMatchObject({ kind: 'user', displayName: '我' });
    expect(data.identities[1]).toMatchObject({ kind: 'bot', id: 'b1', displayName: 'Bot一号', online: true });
    expect(data.defaultActiveId).toBe('me');
  });

  it('uses real human entry from mine as the user identity (first), fallback synthetic only when no human', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'human', bot_id: 'human-1', name: '风太', avatar_url: 'avatar-x', status: 'online' },
          { kind: 'bot', bot_id: 'b1', name: 'Bot一号', avatar_url: 'u1', status: 'online' },
          { kind: 'bot', bot_id: 'b2', name: 'Bot二号', avatar_url: 'u2', status: 'offline' },
        ],
        total: 3,
        offset: 0,
        limit: 20,
      },
    });
    const res = await identityService.loadIdentities();
    expect(res.ok).toBe(true);
    const data = (res as SuccessResult).data;
    // human first — real name/avatar, not synthetic '我'
    expect(data.identities[0]).toMatchObject({
      kind: 'user',
      id: 'human-1',
      displayName: '风太',
      avatarUrl: 'avatar-x',
      online: true,
    });
    // bots follow
    expect(data.identities[1]).toMatchObject({ kind: 'bot', id: 'b1' });
    expect(data.identities[2]).toMatchObject({ kind: 'bot', id: 'b2' });
    // default active = real me id (not literal 'me')
    expect(data.defaultActiveId).toBe('human-1');
  });

  it('maps bot status/reachability: hidden→不可聊天状态, unreachable→红点可达性', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-online', name: '在线Bot', status: 'online', reachability: 'reachable' },
          { kind: 'bot', bot_id: 'b-hidden', name: '隐身Bot', status: 'hidden', reachability: 'unreachable' },
        ],
        total: 2,
        offset: 0,
        limit: 20,
      },
    });
    const res = await identityService.loadIdentities();
    const data = (res as SuccessResult).data;
    // 第一个是合成的「我」，b-online / b-hidden 紧随其后
    const online = data.identities[1];
    const hidden = data.identities[2];
    expect(online).toMatchObject({ id: 'b-online', online: true, status: 'online', reachability: 'reachable' });
    expect(hidden).toMatchObject({ id: 'b-hidden', online: false, status: 'hidden', reachability: 'unreachable' });
  });

  it('returns friendly error with canRetry on backend failure', async () => {
    listMyBots.mockRejectedValue({ status: 503, message: 'upstream is down' });
    const res = await identityService.loadIdentities();
    expect(res.ok).toBe(false);
    const error = (res as FailureResult).error;
    expect(error.canRetry).toBe(true);
    expect(error.friendlyMessage).toContain('加载');
  });

  it('prefers persisted lastIdentityId when it matches identities', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [{ kind: 'bot', bot_id: 'b1', name: 'Bot一号', status: 'online' }],
        total: 1,
        offset: 0,
        limit: 20,
      },
    });
    window.localStorage.setItem('teamclaw:workspace:lastIdentityId', 'b1');
    const res = await identityService.loadIdentities();
    expect((res as SuccessResult).data.defaultActiveId).toBe('b1');
    window.localStorage.removeItem('teamclaw:workspace:lastIdentityId');
  });

  it('只返回 mine 接口身份，不注入测试用户身份', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [{ kind: 'bot', bot_id: 'b1', name: 'Bot一号', status: 'online' }],
        total: 1,
        offset: 0,
        limit: 20,
      },
    });
    const res = await identityService.loadIdentities();
    const data = (res as SuccessResult).data;
    expect(data.identities.find((i) => i.id === 'test-user')).toBeUndefined();
  });

  it('mine 失败时仍返回错误(测试用户由上层兜底注入,见 useWorkspace)', async () => {
    listMyBots.mockRejectedValue({ status: 503 });
    const res = await identityService.loadIdentities();
    expect(res.ok).toBe(false);
  });

  it('并发调用复用同一次 bots/mine 请求(模块级单飞)', async () => {
    let calls = 0;
    listMyBots.mockImplementation(async () => {
      calls++;
      return {
        code: 20000,
        message: '',
        request_id: 'r',
        data: {
          items: [{ kind: 'bot', bot_id: 'b1', name: 'Bot一号', status: 'online' }],
          total: 1,
          offset: 0,
          limit: 20,
        },
      };
    });
    const [r1, r2] = await Promise.all([identityService.loadIdentities(), identityService.loadIdentities()]);
    expect(calls).toBe(1);
    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);
  });
});
