/** @jest-environment jsdom */
import * as ownedBotController from '@/services/backendApi/bots/botController';
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
jest.mock('@/services/backendApi/bots/botController');
type SuccessResult = { ok: true; data: LoadIdentitiesResult };
type FailureResult = { ok: false; error: DomainError };
const listMyBots = (botController as unknown as { listMyBots: jest.Mock<any> }).listMyBots;
const listBots = (ownedBotController as unknown as { listBots: jest.Mock<any> }).listBots;

beforeEach(() => {
  listMyBots.mockReset();
  listBots.mockReset();
  listBots.mockResolvedValue({
    code: 200000,
    message: '',
    request_id: 'r-engine',
    data: { items: [], total: 0, offset: 0, limit: 100 },
  });
});

describe('identityService.loadIdentities', () => {
  it('merges a user identity with mine bots, defaulting active to first bot when no localStorage', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b1', name: 'Bot一号', avatar_url: 'u1', status: 'online', engine: 'OpenClaw' },
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
    expect(data.identities[1]).toMatchObject({
      kind: 'bot',
      id: 'b1',
      displayName: 'Bot一号',
      online: true,
      engine: 'OpenClaw',
    });
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
          { kind: 'bot', bot_id: 'b1', name: 'Bot一号', avatar_url: 'u1', status: 'online', engine: 'OpenClaw' },
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

  it('补充 mine 缺失的 engine：从通用 bots 接口按 Bot ID 回填引擎', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [{ kind: 'bot', bot_id: 'b1:owner-1', name: 'Bot一号', status: 'online' }],
        total: 1,
        offset: 0,
        limit: 20,
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [{ bot_id: 'b1', bot_name: 'Bot一号', engine: 'Hermes', bot_type: 'service' }],
        total: 1,
        offset: 0,
        limit: 100,
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toContainEqual(
      expect.objectContaining({ id: 'b1:owner-1', kind: 'bot', engine: 'Hermes', botType: 'service' }),
    );
    expect(listBots).toHaveBeenCalledWith({ page: 1, page_size: 100 });
  });

  it('兼容 bots 接口的 name、active_engine、engine_type 字段，避免引擎信息降级为空', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-active', name: 'Active Engine Bot', status: 'online' },
          { kind: 'bot', bot_id: 'b-type', name: 'Engine Type Bot', status: 'online' },
        ],
        total: 2,
        offset: 0,
        limit: 20,
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [
          { bot_id: 'b-active', name: 'Active Engine Bot', active_engine: 'OpenClaw', bot_type: 'personal' },
          { bot_id: 'b-type', name: 'Engine Type Bot', engine_type: 'Hermes', bot_type: 'desktop' },
        ],
        total: 2,
        offset: 0,
        limit: 100,
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'b-active', engine: 'OpenClaw', botType: 'personal' }),
        expect.objectContaining({ id: 'b-type', engine: 'Hermes', botType: 'desktop' }),
      ]),
    );
  });

  it('优先使用 engine 对象中的真实引擎类型，不把 provider/name 映射成引擎', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-claude-object', name: 'Claude Bot', status: 'online' },
          { kind: 'bot', bot_id: 'b-claude-string', name: 'Claude String Bot', status: 'online' },
          { kind: 'bot', bot_id: 'b-provider-fallback', name: 'Provider Fallback Bot', status: 'online' },
        ],
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [
          {
            bot_id: 'b-claude-object',
            engine: { name: 'TeamClaw网关', type: 'claude_code' },
            bot_type: 'service',
          },
          { bot_id: 'b-claude-string', engine: 'claude_code', bot_type: 'personal' },
          { bot_id: 'b-provider-fallback', provider: { name: 'TeamClaw网关' }, bot_type: 'desktop' },
        ],
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'b-claude-object', engine: 'claude_code', botType: 'service' }),
        expect.objectContaining({ id: 'b-claude-string', engine: 'claude_code', botType: 'personal' }),
        expect.objectContaining({ id: 'b-provider-fallback', engine: undefined, botType: 'desktop' }),
      ]),
    );
  });

  it('始终以 bots 接口的 engine 覆盖 mine 中的展示名称，避免显示 TeamClaw 网关', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-openclaw', name: 'OpenClaw Bot', status: 'online', engine: 'TeamClaw网关' },
          { kind: 'bot', bot_id: 'b-claude', name: 'Claude Bot', status: 'online', engine: 'TeamClaw网关' },
        ],
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [
          { bot_id: 'b-openclaw', bot_name: 'OpenClaw Bot', engine: 'openclaw' },
          { bot_id: 'b-claude', bot_name: 'Claude Bot', engine: 'claude_code' },
        ],
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'b-openclaw', engine: 'openclaw' }),
        expect.objectContaining({ id: 'b-claude', engine: 'claude_code' }),
      ]),
    );
  });

  it('bots 接口命中但缺少 engine 时不回退到 mine 中的 provider 展示名称', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-no-engine', name: 'No Engine Bot', status: 'online', engine: 'TeamClaw网关' },
        ],
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [{ bot_id: 'b-no-engine', bot_name: 'No Engine Bot', bot_type: 'personal' }],
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toContainEqual(
      expect.objectContaining({ id: 'b-no-engine', engine: undefined, botType: 'personal' }),
    );
  });
  it('兼容 bots 接口 data 直接为数组、id/botId/uuid 以及嵌套引擎字段', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'bot', bot_id: 'b-array', name: '数组 Bot', status: 'online' },
          { kind: 'bot', bot_id: 'b-nested', name: '嵌套 Bot', status: 'online' },
          { kind: 'bot', bot_id: 'b-runtime', name: '运行时 Bot', status: 'online' },
        ],
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: [
        { id: 'b-array', engine: { name: 'OpenClaw' }, bot_type: 'personal' },
        { botId: 'b-nested', engine_info: { type: 'Hermes' }, bot_type: 'service' },
        { uuid: 'b-runtime', runtime: { engine: 'ClaudeCode' }, bot_type: 'desktop' },
      ],
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'b-array', engine: 'OpenClaw', botType: 'personal' }),
        expect.objectContaining({ id: 'b-nested', engine: 'Hermes', botType: 'service' }),
        expect.objectContaining({ id: 'b-runtime', engine: 'ClaudeCode', botType: 'desktop' }),
      ]),
    );
  });

  it('兼容 bots 接口 data.bots 列表结构并回填引擎与 Bot 类型', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [{ kind: 'bot', bot_id: 'b-data-bots', name: 'Data Bots', status: 'online' }],
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      data: { bots: [{ bot_id: 'b-data-bots', engine: 'OpenClaw', bot_type: 'personal' }] },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toContainEqual(
      expect.objectContaining({ id: 'b-data-bots', engine: 'OpenClaw', botType: 'personal' }),
    );
  });

  it('兼容 bots 接口外层 items 列表结构并回填引擎与 Bot 类型', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      items: [{ kind: 'bot', bot_id: 'b-root-items', name: 'Root Items', status: 'online' }],
    });
    listBots.mockResolvedValue({
      code: 200000,
      items: [{ bot_id: 'b-root-items', engine: 'Hermes', bot_type: 'service' }],
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toContainEqual(
      expect.objectContaining({ id: 'b-root-items', engine: 'Hermes', botType: 'service' }),
    );
  });

  it('uses the human identity from mine as the explicit user scope when enriching Bot metadata', async () => {
    listMyBots.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: {
        items: [
          { kind: 'human', bot_id: 'human_447147', name: '风太', status: 'online' },
          { kind: 'bot', bot_id: 'b1', name: 'Bot一号', status: 'online' },
        ],
        total: 2,
        offset: 0,
        limit: 20,
      },
    });
    listBots.mockResolvedValue({
      code: 200000,
      message: '',
      request_id: 'r-engine',
      data: {
        items: [{ bot_id: 'b1', bot_name: 'Bot一号', engine: 'Hermes', bot_type: 'service' }],
        total: 1,
        offset: 0,
        limit: 100,
      },
    });

    const res = await identityService.loadIdentities();

    expect(res.ok).toBe(true);
    expect((res as SuccessResult).data.identities).toContainEqual(
      expect.objectContaining({ id: 'b1', engine: 'Hermes', botType: 'service' }),
    );
    expect(listBots).toHaveBeenCalledWith({ page: 1, page_size: 100, user_id: '447147' });
  });

  it('maps bot runtime status and group-chat reachability independently', async () => {
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
