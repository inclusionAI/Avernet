/** @jest-environment node */
// BCS v1 候选契约消费。契约权威：apps/bcs/api-contracts/v1/openapi/bots.yaml 的
// list_bot_candidates / search_bot_candidates（后者经 internal.yaml 挂载于
// /api/v1/collaboration/bots/{bot_id}/candidates/search），信封结构见 domain-models.yaml
// 的 BotCandidatePageEnvelope / BotCandidateSearchEnvelope，成功 code 恒为 20000。
import {
  BCS_CANDIDATE_SEARCH_PATH,
  BCS_CANDIDATES_PATH,
  listBcsBotCandidates,
  resolveHumanBcsActorId,
  searchBcsCandidates,
} from '@/services/bcs/botCandidatesController';
import * as httpClient from '@/services/backendApi/httpClient';

// 只替换请求出口 backendRequest（同源 Cookie / 登录跳转 / 取消语义由其真实实现承载），
// 保留真实 BackendRequestError 类型供失败路径断言。
jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));

const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock }).backendRequest;
const { BackendRequestError } = httpClient;

/** BCS PhysicalBot 候选投影（对齐 domain-models.yaml BotCandidate/PhysicalBot 的必填字段）。 */
const CANDIDATE_BOT = {
  bot_id: 'assistant:owner-8',
  name: '规划助手',
  avatar_url: null,
  created_by: '8',
  descriptor: { summary: '擅长任务规划与分解' },
  visibility: 'protected' as const,
  user_visibility: 'public' as const,
  status: 'online' as const,
};

const LIST_ENVELOPE = {
  code: 20000,
  message: 'ok',
  data: { items: [{ bot: CANDIDATE_BOT, is_friend: false }], total: 34, offset: 0, limit: 24 },
  request_id: 'req-1',
};

const SEARCH_ENVELOPE = {
  code: 20000,
  message: 'ok',
  data: {
    items: [{ bot: CANDIDATE_BOT, is_friend: false, tags: {}, score: 0.87, short_profile: '擅长规划' }],
    search_mode: 'semantic',
  },
  request_id: 'req-2',
};

beforeEach(() => {
  backendRequest.mockReset();
});

describe('BCS 候选路径构造', () => {
  it('列表与搜索路径按 actorId 做路径段编码', () => {
    expect(BCS_CANDIDATES_PATH('human_7')).toBe('/openapi/v1/collaboration/bots/human_7/candidates');
    expect(BCS_CANDIDATES_PATH('a b/c')).toBe('/openapi/v1/collaboration/bots/a%20b%2Fc/candidates');
    expect(BCS_CANDIDATE_SEARCH_PATH('assistant:owner-7')).toBe(
      '/api/v1/collaboration/bots/assistant%3Aowner-7/candidates/search',
    );
  });
});

describe('listBcsBotCandidates 普通候选列表', () => {
  it('Human 视角发 human_<authenticated-id>，只带 purpose=discovery 与名称/分页参数', async () => {
    backendRequest.mockResolvedValueOnce(LIST_ENVELOPE);

    const page = await listBcsBotCandidates('human_7', { name: '规划', offset: 0, limit: 24 });

    expect(page.data.items[0].bot.bot_id).toBe('assistant:owner-8');
    expect(backendRequest).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/bots/human_7/candidates',
      expect.objectContaining({ method: 'GET', injectUserId: false }),
    );
    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/human_7/candidates', {
      method: 'GET',
      params: { purpose: 'discovery', name: '规划', offset: 0, limit: 24 },
      injectUserId: false,
      signal: undefined,
    });
    // 校验通过的 20000 信封保真返回，页元数据不伪造成功
    expect(page.code).toBe(20000);
    expect(page.data.items[0].is_friend).toBe(false);
    expect(page.data.total).toBe(34);
    expect(page.data.offset).toBe(0);
    expect(page.data.limit).toBe(24);
  });

  it('Bot 视角直接使用所选完整 UUID 作为视角 actor', async () => {
    backendRequest.mockResolvedValueOnce(LIST_ENVELOPE);

    await listBcsBotCandidates('20260811_lklnq6d0:900003', { offset: 60, limit: 40 });

    expect(backendRequest).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/bots/20260811_lklnq6d0%3A900003/candidates',
      expect.objectContaining({
        method: 'GET',
        injectUserId: false,
        params: { purpose: 'discovery', offset: 60, limit: 40 },
      }),
    );
  });

  it('resolveHumanBcsActorId 从 humanBotContext.userId 规范生成 human_<id>', async () => {
    expect(resolveHumanBcsActorId('7')).toBe('human_7');
    // 剥 human_ 前缀的幂等输入同样命中 canonical Actor（与 bots/mine human 项 bot_id 同构）
    expect(resolveHumanBcsActorId('human_7')).toBe('human_7');
    // 'me' 仅是展示占位身份，绝不能生成 human_me 伪造 Actor
    expect(() => resolveHumanBcsActorId('me')).toThrow();

    backendRequest.mockResolvedValueOnce(LIST_ENVELOPE);
    await listBcsBotCandidates(resolveHumanBcsActorId('7'), { offset: 0, limit: 24 });

    expect(backendRequest.mock.calls[0][0]).toBe('/openapi/v1/collaboration/bots/human_7/candidates');
  });

  it('空/空白视角 actor 直接拒绝，不发请求', async () => {
    await expect(listBcsBotCandidates('   ', {})).rejects.toThrow();
    expect(() => resolveHumanBcsActorId(' ')).toThrow(/userId/);
    expect(backendRequest).not.toHaveBeenCalled();
  });

  it('name 为空/空白时不上送名称过滤（空白视同缺省，不注入 user_id）', async () => {
    backendRequest.mockResolvedValueOnce(LIST_ENVELOPE);

    await listBcsBotCandidates('assistant:owner-7', { name: '   ', offset: 0, limit: 20 });

    const [, options] = backendRequest.mock.calls[0] as unknown as [
      string,
      { params: Record<string, unknown>; injectUserId?: unknown },
    ];
    expect(options.params).toEqual({ purpose: 'discovery', offset: 0, limit: 20 });
    expect(options.injectUserId).toBe(false);
  });

  it('缺省分页只带 purpose=discovery；AbortSignal 原样透传', async () => {
    const signal = new AbortController().signal;
    backendRequest.mockResolvedValueOnce(LIST_ENVELOPE);

    await listBcsBotCandidates('assistant:owner-7', {}, signal);

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/bots/assistant%3Aowner-7/candidates', {
      method: 'GET',
      params: { purpose: 'discovery' },
      injectUserId: false,
      signal,
    });
    const [, options] = backendRequest.mock.calls[0] as unknown as [string, { signal?: AbortSignal }];
    expect(options.signal).toBe(signal);
  });
});

describe('listBcsBotCandidates 失败不伪装为空列表', () => {
  it('HTTP 401 拒绝原样上抛，不吞成空页', async () => {
    backendRequest.mockRejectedValueOnce(
      new BackendRequestError('登录态失效，正在跳转登录…', {
        status: 401,
        apiPath: BCS_CANDIDATES_PATH('human_7'),
      }),
    );

    await expect(listBcsBotCandidates('human_7', { offset: 0, limit: 24 })).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 401,
    });
  });

  it('非 20000 的错误信封（HTTP 200 业务失败码）拒绝上抛并保留 apiPath', async () => {
    backendRequest.mockResolvedValue({ code: 50000, message: 'internal error', data: null });

    await expect(listBcsBotCandidates('human_7', {})).rejects.toBeInstanceOf(BackendRequestError);
    await expect(listBcsBotCandidates('human_7', {})).rejects.toThrow('internal error');
    await expect(listBcsBotCandidates('human_7', {})).rejects.toMatchObject({
      apiPath: '/openapi/v1/collaboration/bots/human_7/candidates',
    });
  });

  it('code=20100 或字符串形态 "20000" 均按非 20000 信封拒绝', async () => {
    backendRequest.mockResolvedValueOnce({ ...LIST_ENVELOPE, code: 20100 });
    await expect(listBcsBotCandidates('human_7', {})).rejects.toBeInstanceOf(BackendRequestError);

    backendRequest.mockResolvedValueOnce({ ...LIST_ENVELOPE, code: '20000' });
    await expect(listBcsBotCandidates('human_7', {})).rejects.toBeInstanceOf(BackendRequestError);
  });

  it.each([
    ['信封缺 data', { code: 20000, message: 'ok' }],
    ['data 为 null', { code: 20000, message: 'ok', data: null }],
    ['items 非数组', { code: 20000, data: { items: {}, total: 1, offset: 0, limit: 20 } }],
    ['total 非数值', { code: 20000, data: { items: [], total: '34', offset: 0, limit: 20 } }],
    ['total 为负数', { code: 20000, data: { items: [], total: -1, offset: 0, limit: 20 } }],
    ['offset 为负数', { code: 20000, data: { items: [], total: 0, offset: -1, limit: 20 } }],
    ['limit 为 0', { code: 20000, data: { items: [], total: 0, offset: 0, limit: 0 } }],
    ['候选项缺 bot', { code: 20000, data: { items: [{ is_friend: false }], total: 1, offset: 0, limit: 20 } }],
    ['候选项缺 is_friend', { code: 20000, data: { items: [{ bot: CANDIDATE_BOT }], total: 1, offset: 0, limit: 20 } }],
    [
      'is_friend 非布尔',
      { code: 20000, data: { items: [{ bot: CANDIDATE_BOT, is_friend: 'yes' }], total: 1, offset: 0, limit: 20 } },
    ],
    [
      'bot_id 为空白',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, bot_id: '   ' }, is_friend: false }], total: 1, offset: 0, limit: 20 },
      },
    ],
    [
      'status 非法',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, status: 'offline' }, is_friend: false }], total: 1, offset: 0, limit: 20 },
      },
    ],
    [
      'visibility 非法',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, visibility: 'friends' }, is_friend: false }], total: 1, offset: 0, limit: 20 },
      },
    ],
    [
      'descriptor.summary 缺失',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, descriptor: {} }, is_friend: false }], total: 1, offset: 0, limit: 20 },
      },
    ],
    [
      'avatar_url 非字符串非 null',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, avatar_url: 42 }, is_friend: false }], total: 1, offset: 0, limit: 20 },
      },
    ],
  ] as const)('无效结构拒绝：%s', async (_label, envelope) => {
    backendRequest.mockResolvedValueOnce(envelope);

    await expect(listBcsBotCandidates('human_7', {})).rejects.toBeInstanceOf(BackendRequestError);
  });
});

describe('searchBcsCandidates 智能候选搜索', () => {
  it('走内部 v1 搜索路径并保留 search_mode/score/short_profile', async () => {
    const signal = new AbortController().signal;
    backendRequest.mockResolvedValueOnce(SEARCH_ENVELOPE);

    const result = await searchBcsCandidates('assistant:owner-7', { q: '规划', purpose: 'discovery' }, signal);

    expect(backendRequest).toHaveBeenCalledWith('/api/v1/collaboration/bots/assistant%3Aowner-7/candidates/search', {
      method: 'GET',
      params: { q: '规划', purpose: 'discovery' },
      injectUserId: false,
      signal,
    });
    const [, options] = backendRequest.mock.calls[0] as unknown as [string, { signal?: AbortSignal }];
    expect(options.signal).toBe(signal);
    expect(result.code).toBe(20000);
    expect(result.data.search_mode).toBe('semantic');
    expect(result.data.items).toHaveLength(1);
    expect(result.data.items[0].bot.bot_id).toBe('assistant:owner-8');
    expect(result.data.items[0].score).toBe(0.87);
    expect(result.data.items[0].short_profile).toBe('擅长规划');
  });

  it('未显式指定 purpose 时默认 discovery', async () => {
    backendRequest.mockResolvedValueOnce(SEARCH_ENVELOPE);

    await searchBcsCandidates('assistant:owner-7', { q: '规划' });

    const [, options] = backendRequest.mock.calls[0] as unknown as [string, { params: Record<string, unknown> }];
    expect(options.params).toEqual({ q: '规划', purpose: 'discovery' });
  });

  it('空关键词不上送：按服务端 empty_query 语义本地短路为空结果', async () => {
    const result = await searchBcsCandidates('assistant:owner-7', { q: '   ' });

    expect(backendRequest).not.toHaveBeenCalled();
    expect(result.code).toBe(20000);
    expect(result.data.search_mode).toBe('empty_query');
    expect(result.data.items).toEqual([]);
  });

  it('空关键词也照常校验视角 actor，空白视角不发请求', async () => {
    await expect(searchBcsCandidates('  ', { q: '' })).rejects.toThrow();
    expect(backendRequest).not.toHaveBeenCalled();
  });

  it('name_fallback 结果原样保留（缺省 score/short_profile 不是错误）', async () => {
    backendRequest.mockResolvedValueOnce({
      code: 20000,
      message: 'ok',
      data: { items: [{ bot: CANDIDATE_BOT, is_friend: true }], search_mode: 'name_fallback' },
    });

    const result = await searchBcsCandidates('assistant:owner-7', { q: '规划' });

    expect(result.data.search_mode).toBe('name_fallback');
    expect(result.data.items[0].score).toBeUndefined();
    expect(result.data.items[0].short_profile).toBeUndefined();
    expect(result.data.items[0].is_friend).toBe(true);
  });

  it('401 与错误信封同样拒绝，不伪装为空结果', async () => {
    backendRequest.mockRejectedValueOnce(
      new BackendRequestError('登录态失效，正在跳转登录…', {
        status: 401,
        apiPath: BCS_CANDIDATE_SEARCH_PATH('assistant:owner-7'),
      }),
    );
    await expect(searchBcsCandidates('assistant:owner-7', { q: '规划' })).rejects.toMatchObject({ status: 401 });

    backendRequest.mockResolvedValueOnce({ code: 50000, message: 'internal error', data: null });
    await expect(searchBcsCandidates('assistant:owner-7', { q: '规划' })).rejects.toBeInstanceOf(BackendRequestError);
  });

  it.each([
    ['search_mode 非法', { code: 20000, data: { items: [], search_mode: 'recommend' } }],
    ['data 为 null', { code: 20000, data: null }],
    ['items 非数组', { code: 20000, data: { items: 'none', search_mode: 'semantic' } }],
    [
      'score 非数值',
      { code: 20000, data: { items: [{ bot: CANDIDATE_BOT, is_friend: false, score: 'high' }], search_mode: 'semantic' } },
    ],
    [
      'short_profile 非字符串',
      { code: 20000, data: { items: [{ bot: CANDIDATE_BOT, is_friend: false, short_profile: 123 }], search_mode: 'semantic' } },
    ],
    ['候选项缺 bot', { code: 20000, data: { items: [{ is_friend: false }], search_mode: 'semantic' } }],
    [
      'bot_id 为空白',
      {
        code: 20000,
        data: { items: [{ bot: { ...CANDIDATE_BOT, bot_id: ' ' }, is_friend: false }], search_mode: 'semantic' },
      },
    ],
  ] as const)('无效搜索结构拒绝：%s', async (_label, envelope) => {
    backendRequest.mockResolvedValueOnce(envelope);

    await expect(searchBcsCandidates('assistant:owner-7', { q: '规划' })).rejects.toBeInstanceOf(BackendRequestError);
  });
});
