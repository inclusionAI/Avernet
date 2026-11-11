/** @jest-environment jsdom */
// BCS 原始 offset 游标拼页 + 可选 Backend metadata 过滤的纯服务
// （bcs-standalone-friend-discovery Task 2）。Task 3 由 CollaborationSquareApiAdapter
// 读取能力并接上真实 BCS Controller 与信封检查；本文件按下述 fixture 锁定游标语义：
// 原始下标 0..5 被过滤，6..29 为前 24 条展示，30 为预读项（nextOffset=30）。
import type { BcsCandidateItem } from '@/domain/collaborationSquare/bcsBotMapper';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import {
  BCS_CANDIDATE_SCAN_PAGE_SIZE,
  readPublicBotPage,
  type FetchBcsCandidatePage,
} from '@/services/collaborationSquare/publicBotCursor';
import type { FetchPublicBotMetadata } from '@/services/collaborationSquare/publicBotMetadata';

function candidateAt(rawIndex: number): BcsCandidateItem {
  return {
    bot: {
      bot_id: `assistant:owner-${rawIndex}`,
      name: `候选Bot${rawIndex}`,
      avatar_url: null,
      created_by: '9',
      descriptor: { summary: `第 ${rawIndex} 条候选摘要` },
      visibility: 'public',
      user_visibility: 'public',
      status: 'online',
    },
    is_friend: false,
  };
}

/** 以 BCS 原始 offset 提供虚拟候选页（固定 100 上限由游标下发）。 */
function bcsPageFetcher(total: number, onFetch?: (offset: number) => void): jest.MockedFunction<FetchBcsCandidatePage> {
  return jest.fn(async (actorId: string, query: { offset: number; limit: number }) => {
    void actorId;
    onFetch?.(query.offset);
    const items: BcsCandidateItem[] = [];
    for (let index = query.offset; index < Math.min(query.offset + query.limit, total); index++) {
      items.push(candidateAt(index));
    }
    return { items, total, offset: query.offset, limit: query.limit };
  });
}

/**
 * 模拟已配置 Backend 的 metadata 出口：按请求的身份对返回记录
 * （owner-N → 原始下标 N）；serviceFalseIndices 标记 service,false，其余为 personal。
 */
function metadataFetcher(serviceFalseIndices: number[]): jest.MockedFunction<FetchPublicBotMetadata> {
  return jest.fn(async (userId: string, pairs: { bot_id: string; owner_id: string }[]) => {
    void userId;
    return pairs.map((pair) => {
      const rawIndex = Number(pair.owner_id.replace('owner-', ''));
      const serviceFalse = serviceFalseIndices.includes(rawIndex);
      return {
        bcn_bot_id: `${pair.bot_id}:${pair.owner_id}`,
        bot_id: pair.bot_id,
        owner_id: pair.owner_id,
        bot_name: `候选Bot${rawIndex}`,
        bot_type: serviceFalse ? 'service' : 'personal',
        online_available: serviceFalse ? false : null,
      };
    });
  });
}

function expectedIds(from: number, to: number): string[] {
  const ids: string[] = [];
  for (let index = from; index < to; index++) ids.push(`assistant:owner-${index}`);
  return ids;
}

describe('readPublicBotPage（BCS 原始 offset 游标）', () => {
  const signal = new AbortController().signal;
  const onEnhancementUnavailable = jest.fn();

  beforeEach(() => {
    onEnhancementUnavailable.mockClear();
  });

  it('metadata 返回 owner_name 时展示项回填 ownerName（不丢失 filter 产出的增强对象）', async () => {
    const fetchCandidates = bcsPageFetcher(10);
    const fetchMetadata = jest.fn(async (userId: string, pairs: { bot_id: string; owner_id: string }[]) => {
      void userId;
      return pairs.map((pair) => ({
        bcn_bot_id: `${pair.bot_id}:${pair.owner_id}`,
        bot_id: pair.bot_id,
        owner_id: pair.owner_id,
        owner_name: `归属人-${pair.owner_id}`,
        bot_name: '候选Bot',
        bot_type: 'personal',
        online_available: null,
      }));
    });

    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 5,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    expect(result.items).toHaveLength(5);
    expect(result.items[0].ownerName).toBe('归属人-owner-0');
    expect(result.items[4].ownerName).toBe('归属人-owner-4');
  });

  it('服务过滤后展示页 24 条、按 BCS 原顺序，nextOffset 指向第 25 条合格记录的原始位置（30），下一页不重叠', async () => {
    // 候选页 100 条里多条 metadata service,false（原始下标 0..5），下一 BCS 页仍有可展示项（total=200）。
    const fetchCandidates = bcsPageFetcher(200);
    const fetchMetadata = metadataFetcher([0, 1, 2, 3, 4, 5]);

    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    expect(result.items.map((bot) => bot.id)).toEqual(expectedIds(6, 30));
    expect(result.items).toHaveLength(24);
    expect(result.hasMore).toBe(true);
    // fixture 原始下标 0..5 被过滤；6..29 为前 24 个，30 是预读项。
    expect(result.nextOffset).toBe(30);
    expect(onEnhancementUnavailable).not.toHaveBeenCalled();

    // 上游读：每批固定 ≤100，offset 从 0 开始；显式 signal 透传。
    expect(fetchCandidates).toHaveBeenCalledTimes(1);
    expect(fetchCandidates).toHaveBeenCalledWith('human_7', { offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }, signal);

    // metadata/queries 每批最多 100 对，显式带登录 Human 的 user_id（不用所选 Bot 身份代替）。
    expect(fetchMetadata).toHaveBeenCalledTimes(1);
    const [userIdArg, pairsArg, signalArg] = fetchMetadata.mock.calls[0];
    expect(userIdArg).toBe('7');
    expect(pairsArg).toHaveLength(100);
    expect(pairsArg[6]).toEqual({ bot_id: 'assistant', owner_id: 'owner-6' });
    expect(signalArg).toBe(signal);

    // 下一页从预读项的 BCS 原始位置（30）重新读取，不重叠、不丢失。
    const second = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: result.nextOffset,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
    });
    expect(second.items.map((bot) => bot.id)).toEqual(expectedIds(30, 54));
    expect(second.hasMore).toBe(true);
    expect(second.nextOffset).toBe(54);
    const overlap = second.items.filter((bot) => result.items.some((item) => item.id === bot.id));
    expect(overlap).toEqual([]);
  });

  it('Bot 视角时 metadata 仍显式带登录 Human 的真实 user_id，不得用所选 Bot 身份代替', async () => {
    const fetchCandidates = bcsPageFetcher(40);
    const fetchMetadata = metadataFetcher([9]);

    const result = await readPublicBotPage({
      actorId: 'assistant:owner-7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
    });

    expect(result.items).toHaveLength(24);
    expect(fetchCandidates).toHaveBeenCalledWith(
      'assistant:owner-7',
      { offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE },
      signal,
    );
    expect(fetchMetadata.mock.calls[0][0]).toBe('7');
    // 原始下标 9 的 service,false 候选被拆除，页面从原始下标 10 起按顺序拉平凑满。
    expect(result.items.map((bot) => bot.id)).toEqual([
      ...expectedIds(0, 9),
      ...expectedIds(10, 25),
    ]);
  });

  it('backendEnabled=false（未配置 Backend）时 0 次 metadata 请求，直接按 BCS 原顺序凑整页', async () => {
    const fetchCandidates = bcsPageFetcher(200);
    const fetchMetadata = jest.fn<ReturnType<FetchPublicBotMetadata>, Parameters<FetchPublicBotMetadata>>();

    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: false,
      fetchCandidates,
      fetchMetadata,
      signal,
    });

    expect(result.items.map((bot) => bot.id)).toEqual(expectedIds(0, 24));
    expect(result.items).toHaveLength(24);
    expect(result.hasMore).toBe(true);
    expect(result.nextOffset).toBe(24);
    expect(fetchMetadata).not.toHaveBeenCalled();
    expect(fetchCandidates).toHaveBeenCalledTimes(1);
  });

  it('无候选与全部候选被过滤完时返回 hasMore=false，不重复扫描同一 offset', async () => {
    const emptyCandidates = bcsPageFetcher(0);
    const emptyResult = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates: emptyCandidates,
      fetchMetadata: metadataFetcher([]),
      signal,
    });
    expect(emptyResult.items).toEqual([]);
    expect(emptyResult.hasMore).toBe(false);
    expect(emptyCandidates).toHaveBeenCalledTimes(1);

    // 全部候选 service,false：单页内被过滤尽，hasMore=false 且不重扫同一 offset。
    const filteredAllCandidates = bcsPageFetcher(30);
    const filteredAllResult = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates: filteredAllCandidates,
      fetchMetadata: metadataFetcher(Array.from({ length: 30 }, (_, index) => index)),
      signal,
    });
    expect(filteredAllResult.items).toEqual([]);
    expect(filteredAllResult.hasMore).toBe(false);
    expect(filteredAllCandidates).toHaveBeenCalledTimes(1);
  });

  it('上千条全被过滤的候选：每次上游读 ≤100、扫描受 BCS total 限制，无无限循环', async () => {
    const total = 1024;
    const allFiltered = Array.from({ length: total }, (_, index) => index);
    const fetchCandidates = bcsPageFetcher(total);
    const fetchMetadata = metadataFetcher(allFiltered);

    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
    });

    expect(result.items).toEqual([]);
    expect(result.hasMore).toBe(false);
    // 扫描受 total 限制：11 批各读 ≤100 条即自然终止；每批 metadata 一次、每批 ≤100 对。
    expect(fetchCandidates).toHaveBeenCalledTimes(Math.ceil(total / BCS_CANDIDATE_SCAN_PAGE_SIZE));
    expect(fetchMetadata).toHaveBeenCalledTimes(Math.ceil(total / BCS_CANDIDATE_SCAN_PAGE_SIZE));
    for (const [, query] of fetchCandidates.mock.calls) {
      expect(query.limit).toBe(BCS_CANDIDATE_SCAN_PAGE_SIZE);
    }
    for (const [, pairs] of fetchMetadata.mock.calls) {
      expect(pairs.length).toBeLessThanOrEqual(100);
    }
  });

  it('最坏情况记录：前 1000 条全被过滤时，需 11 批上游读才凑满 24 条展示页', async () => {
    const total = 1024;
    // 原始下标 0..999 全部 service,false；1000..1023 共 24 条合格记录。
    const serviceFalse = Array.from({ length: 1000 }, (_, index) => index);
    const offsetsSeen: number[] = [];
    const fetchCandidates = bcsPageFetcher(total, (offset) => offsetsSeen.push(offset));
    const fetchMetadata = metadataFetcher(serviceFalse);

    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 24,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
    });

    // 10 批全过滤（0..999）+ 第 11 批（1000..1023）恰好 24 条合格 → 11 批凑满展示页；
    // 合格数不足 pageSize+1，hasMore=false。
    expect(offsetsSeen).toEqual([0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]);
    expect(result.items.map((bot) => bot.id)).toEqual(expectedIds(1000, 1024));
    expect(result.items).toHaveLength(24);
    expect(result.hasMore).toBe(false);
    expect(fetchCandidates).toHaveBeenCalledTimes(11);
  });

  it('mid-scan AbortSignal 中止扫描：上抛 AbortError，不再继续读下一批', async () => {
    const controller = new AbortController();
    const fetchCandidates = bcsPageFetcher(1024, (offset) => {
      if (offset === 200) controller.abort();
    });
    // 全部候选 service,false：扫描无法在第一批凑页，必须越过第 3 批才会因中止停止。
    const fetchMetadata = metadataFetcher(Array.from({ length: 1024 }, (_, index) => index));

    await expect(
      readPublicBotPage({
        actorId: 'human_7',
        userId: '7',
        offset: 0,
        pageSize: 24,
        backendEnabled: true,
        fetchCandidates,
        fetchMetadata,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: 'AbortError' });

    // 第 3 批（offset=200）后即止：不重复扫描、无无限循环。
    expect(fetchCandidates).toHaveBeenCalledTimes(3);
  });

  it('Backend 401/403 沿认证错误路径原样上抛，不当作「未部署 Backend」', async () => {
    const fetchCandidates = bcsPageFetcher(200);
    const error = new BackendRequestError('未授权', {
      status: 401,
      apiPath: '/openapi/v1/bots/metadata/queries',
    });
    const fetchMetadata = jest.fn<ReturnType<FetchPublicBotMetadata>, Parameters<FetchPublicBotMetadata>>(
      async () => {
        throw error;
      },
    );

    await expect(
      readPublicBotPage({
        actorId: 'human_7',
        userId: '7',
        offset: 0,
        pageSize: 24,
        backendEnabled: true,
        fetchCandidates,
        fetchMetadata,
        signal,
        onEnhancementUnavailable,
      }),
    ).rejects.toBe(error);
    expect(onEnhancementUnavailable).not.toHaveBeenCalled();
  });

  it('metadata 网络/5xx 失败：保留 BCS 候选并提示增强不可用，本次读取不再重试 metadata', async () => {
    const total = 250;
    const fetchCandidates = bcsPageFetcher(total);
    const fetchMetadata = jest.fn<ReturnType<FetchPublicBotMetadata>, Parameters<FetchPublicBotMetadata>>(
      async () => {
        throw new BackendRequestError('服务暂不可用', {
          status: 500,
          apiPath: '/openapi/v1/bots/metadata/queries',
        });
      },
    );

    // pageSize=150 > 单批 100，保证首个降级批次后继续扫描第二页，验证「继续保留 BCS 候选」
    // 与「同一读取内 metadata 只尝试一次（不重复向不可用服务发请求）」。
    const result = await readPublicBotPage({
      actorId: 'human_7',
      userId: '7',
      offset: 0,
      pageSize: 150,
      backendEnabled: true,
      fetchCandidates,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    expect(result.items.map((bot) => bot.id)).toEqual(expectedIds(0, 150));
    expect(result.items).toHaveLength(150);
    expect(result.hasMore).toBe(true);
    expect(result.nextOffset).toBe(150);
    expect(fetchMetadata).toHaveBeenCalledTimes(1);
    expect(onEnhancementUnavailable).toHaveBeenCalledTimes(1);
  });
});
