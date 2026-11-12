/** @jest-environment jsdom */
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import { AceLoginRedirectError, BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';
import { BCS_CANDIDATE_SCAN_PAGE_SIZE } from '@/services/collaborationSquare/publicBotCursor';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { BotMetadataQueryBody } from '@/services/backendApi/bots/botController';
import { describe, expect, it } from '@jest/globals';
import {
  mockedListBcsBotCandidates,
  mockedSearchBcsCandidates,
  mockedListBotMetadata,
  mockedBackendCatalogSearch,
  mockedBackendCatalogDiscovery,
  mockedListFriendConnections,
  mockedListFriendConnectionRequests,
  humanContext,
  setPublicBotBackendEnabled,
  candidateAt,
  mockBcsCandidatesPage,
  mockBotMetadata,
  mockEmptySentPendingRequests,
  emptyFriendRequestsForDiscovery,
  idsOf,
} from './collaborationSquareApiAdapter.fixtures';

describe('CollaborationSquareApiAdapter listBotPage（BCS v1 公开候选）', () => {
  it('Human 视角生成 human_<user-id> Actor、上游整批 ≤20：purpose=discovery 交给 Controller 组装', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(1);
    mockEmptySentPendingRequests();
    const adapter = new CollaborationSquareApiAdapter();

    const page = await adapter.listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(mockedListBcsBotCandidates).toHaveBeenCalledWith(
      'human_900003',
      expect.objectContaining({ offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }),
      undefined,
    );
    // 普通列表/名称过滤不再访问 Backend catalog。
    expect(mockedBackendCatalogSearch).not.toHaveBeenCalled();
    expect(mockedBackendCatalogDiscovery).not.toHaveBeenCalled();
    expect(page.items[0]).toEqual({
      id: 'assistant:owner-0',
      // 缺省头像与 mapper 共用同一默认解析器（种子语义随 base 的 resolveBotAvatarUrl 演进，
      // 如协作身份 :归属 后缀是否参与种子由该函数统一决定，测试不重复固化其实现细节）。
      avatarUrl: resolveBotAvatarUrl(null, 'assistant:owner-0'),
      name: '候选Bot0',
      ownerName: '未公开',
      description: '第 0 条候选摘要',
      capabilities: [],
      relationshipStatus: 'none',
      audience: undefined,
      bcsStatus: 'online',
    });
  });

  it('名称过滤下发给 BCS name 参数，不访问 Backend catalog', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(2);
    mockEmptySentPendingRequests();

    await new CollaborationSquareApiAdapter().listBotPage(
      { search: '规划', offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(mockedListBcsBotCandidates).toHaveBeenCalledWith(
      'human_900003',
      expect.objectContaining({ name: '规划', offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }),
      undefined,
    );
    expect(mockedBackendCatalogSearch).not.toHaveBeenCalled();
  });

  it('Bot 视角直接使用所选完整身份（assistant:owner-7）作为 BCS 候选 Actor', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(1);
    mockEmptySentPendingRequests();

    await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'bot', viewerActorId: 'assistant:owner-7' },
      humanContext,
    );

    expect(mockedListBcsBotCandidates).toHaveBeenCalledWith(
      'assistant:owner-7',
      expect.objectContaining({ offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }),
      undefined,
    );
  });

  it('AbortSignal 原样透传给 BCS Controller', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(0);
    mockEmptySentPendingRequests();
    const signal = new AbortController().signal;

    await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
      signal,
    );

    expect(mockedListBcsBotCandidates).toHaveBeenCalledWith(
      'human_900003',
      expect.objectContaining({ offset: 0, limit: BCS_CANDIDATE_SCAN_PAGE_SIZE }),
      signal,
    );
  });

  it('关闭 Backend 增强时 0 次 metadata 请求；BCS 游标分页不漏页且 hidden Bot 不额外排除', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(30, {
      candidateAt: (rawIndex) => candidateAt(rawIndex, { status: rawIndex === 5 ? 'hidden' : 'online' }),
    });
    mockEmptySentPendingRequests();
    const adapter = new CollaborationSquareApiAdapter();

    const first = await adapter.listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );
    expect(first.items).toHaveLength(24);
    expect(first.hasMore).toBe(true);
    expect(first.nextOffset).toBe(24);
    // 普通列表不额外排除 hidden Bot（hidden 只由旧分享链接守卫判定）。
    expect(first.items[5].bcsStatus).toBe('hidden');
    expect(mockedListBotMetadata).not.toHaveBeenCalled();

    const second = await adapter.listBotPage(
      { offset: first.nextOffset, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );
    expect(idsOf(second.items)).toEqual([
      'assistant:owner-24',
      'assistant:owner-25',
      'assistant:owner-26',
      'assistant:owner-27',
      'assistant:owner-28',
      'assistant:owner-29',
    ]);
    expect(second.hasMore).toBe(false);
  });

  it('开启 Backend 增强时按整批身份对查 metadata：service,false 被滤除，native Bot（无可解析身份对）保留', async () => {
    setPublicBotBackendEnabled(true);
    mockBcsCandidatesPage(30, {
      candidateAt: (rawIndex) =>
        rawIndex === 12
          ? candidateAt(rawIndex, { bot_id: 'native-bot-12', name: '原生 Bot' })
          : candidateAt(rawIndex),
    });
    mockBotMetadata(new Set(['assistant:owner-9']));
    mockEmptySentPendingRequests();

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    // service,false 的 assistant:owner-9 被滤除，页内按 BCS 原始顺序补齐后续条目；native Bot 不被
    // 当作「没查到 Backend 元数据」而排除。
    expect(page.items.map((bot) => bot.id)).toContain('native-bot-12');
    expect(page.items.map((bot) => bot.id)).not.toContain('assistant:owner-9');
    expect(page.items).toHaveLength(24);
    expect(page.hasMore).toBe(true);
    // 第 25 条合格记录（原始下标 25：0..8 + 10..24 为前 24 条合格）固定 nextOffset。
    expect(page.nextOffset).toBe(25);
    expect(mockedListBotMetadata).toHaveBeenCalledTimes(2);
    const [metadataParams, metadataBody, metadataSignal] = mockedListBotMetadata.mock.calls[0];
    // metadata 显式带登录 Human 的 user_id，page_size 覆盖整批身份对（首批 native bot 无冒号，19 对）。
    expect(metadataParams).toMatchObject({ user_id: '900003', page_size: 19 });
    const pairs = (metadataBody as BotMetadataQueryBody).bots;
    expect(pairs).toHaveLength(19);
    expect(pairs).toContainEqual(expect.objectContaining({ bot_id: 'assistant', owner_id: 'owner-0' }));
    expect(metadataSignal).toBeUndefined();
  });

  it('普通列表的 is_friend 直接来自 BCS，仅补查 sent/pending 申请回填 applying', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(2, {
      candidateAt: (rawIndex) => candidateAt(rawIndex, { is_friend: rawIndex === 0 }),
    });
    mockedListFriendConnectionRequests.mockResolvedValue({
      code: 20000 as const,
      message: 'ok',
      data: {
        items: [
          {
            request_id: 'request-1',
            from_actor: { type: 'human', id: '900003' },
            to_actor: { type: 'bot', id: 'assistant:owner-1' },
            status: 'pending',
          },
        ],
        total: 1,
      },
    });

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    // Search/候选路径不重复读取已建立好友连接。
    expect(mockedListFriendConnections).not.toHaveBeenCalled();
    expect(mockedListFriendConnectionRequests).toHaveBeenCalledWith(
      {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: '900003',
        page: 1,
        page_size: 100,
      },
      undefined,
    );
    expect(page.items.map((bot) => bot.relationshipStatus)).toEqual(['friend', 'applying']);
  });

  it('BCS 候选按 owner_user_id 而非 created_by 匹配登录用户，标记自有 Bot', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(1, {
      candidateAt: (index) => ({ ...candidateAt(index), owner_user_id: '900003' }),
    });
    mockEmptySentPendingRequests();

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(page.items[0].isOwnedByLoggedInUser).toBe(true);
  });

  it('BCS 控制器失败沿既有错误策略：401/AceLogin→unauthenticated，AbortError 原样透传', async () => {
    setPublicBotBackendEnabled(false);
    mockedListBcsBotCandidates.mockRejectedValueOnce(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/collaboration/bots/human_900003/candidates' }),
    );
    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });

    mockedListBcsBotCandidates.mockRejectedValueOnce(new AceLoginRedirectError());
    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });

    const abortError = new DOMException('aborted', 'AbortError');
    mockedListBcsBotCandidates.mockRejectedValueOnce(abortError);
    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toBe(abortError);
  });

  it('metadata 401/403 沿鉴权路径上抛，不当成「增强不可用」', async () => {
    setPublicBotBackendEnabled(true);
    mockBcsCandidatesPage(30);
    mockedListBotMetadata.mockRejectedValue(new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/bots/metadata/queries' }));

    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });

    mockedListBotMetadata.mockRejectedValue(new BackendRequestError('forbidden', { status: 403, apiPath: '/openapi/v1/bots/metadata/queries' }));
    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'forbidden' });
    expect(useErrorNotifyStore.getState().drain()).toEqual([]);
  });

  it('oauth-provider 登录失效（AceLoginRedirectError）的 metadata 失败同样沿鉴权路径，不降级为增强不可用', async () => {
    setPublicBotBackendEnabled(true);
    mockBcsCandidatesPage(30);
    mockedListBotMetadata.mockRejectedValue(new AceLoginRedirectError());

    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });
    // 鉴权语义不得被吞成「增强不可用」提示。
    expect(useErrorNotifyStore.getState().drain()).toEqual([]);
  });

  it('metadata 网络/5xx 失败保留 BCS 候选并提示增强不可用', async () => {
    setPublicBotBackendEnabled(true);
    mockBcsCandidatesPage(30);
    mockEmptySentPendingRequests();
    mockedListBotMetadata.mockRejectedValue(
      new BackendRequestError('服务暂不可用', { status: 500, apiPath: '/openapi/v1/bots/metadata/queries' }),
    );

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    // 5xx 时保留整批 BCS 候选（不过滤），页结构与游标照常。
    expect(page.items).toHaveLength(24);
    expect(page.items[9]).toEqual(expect.objectContaining({ id: 'assistant:owner-9' }));
    expect(page.nextOffset).toBe(24);
    expect(page.hasMore).toBe(true);
    // Service 层只 enqueue（不直调 toast）：观察者按 id=toastKey 投递同款文案并去重合并。
    const drained = useErrorNotifyStore.getState().drain();
    expect(drained).toHaveLength(1);
    expect(drained[0].toastKey).toBe('collaboration-square-bot-metadata-unavailable');
    expect(drained[0].message).toBe('Bot 展示增强暂不可用，已展示基础信息');
    expect(drained[0].cancelled).toBeUndefined();
  });

  it('metadata AbortError 原样传播（不提示、不静默成功）', async () => {
    setPublicBotBackendEnabled(true);
    mockBcsCandidatesPage(30);
    const abortError = new DOMException('aborted', 'AbortError');
    mockedListBotMetadata.mockRejectedValue(abortError);

    await expect(
      new CollaborationSquareApiAdapter().listBotPage(
        { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toBe(abortError);
    expect(useErrorNotifyStore.getState().drain()).toEqual([]);
  });

  it('无候选返回空页 hasMore=false，不重复扫描同一 offset', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(0);
    mockEmptySentPendingRequests();

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(page.items).toEqual([]);
    expect(page.hasMore).toBe(false);
    expect(page.nextOffset).toBe(0);
    expect(mockedListBcsBotCandidates).toHaveBeenCalledTimes(1);
    // 空页不发起好友关系请求。
    expect(mockedListFriendConnectionRequests).not.toHaveBeenCalled();
  });
});

describe('CollaborationSquareApiAdapter discoverBots（BCS 内部 v1 candidates/search）', () => {
  const searchEnvelope = (items: Array<ReturnType<typeof candidateAt> & { score?: number; short_profile?: string }>, searchMode: 'semantic' | 'name_fallback') => ({
    code: 20000 as const,
    message: 'ok',
    data: { items, search_mode: searchMode },
  });

  it('Bot 视角智能搜索调用 BCS 内部 search：q/purpose=discovery、AbortSignal 透传，semantic 保留分数/短画像', async () => {
    setPublicBotBackendEnabled(false);
    mockedSearchBcsCandidates.mockResolvedValueOnce(
      searchEnvelope(
        [
          {
            bot: candidateAt(0, { bot_id: 'assistant:owner-8', name: '规划助手' }).bot,
            is_friend: false,
            score: 0.87,
            short_profile: '擅长任务规划与分解',
          },
        ],
        'semantic',
      ),
    );
    mockedListFriendConnections.mockResolvedValue({ code: 20000 as const, message: 'ok', data: { items: [], total: 0 } });
    mockEmptySentPendingRequests();
    const signal = new AbortController().signal;

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '规划', viewerActorType: 'bot', viewerActorId: 'assistant:owner-7' },
      humanContext,
      signal,
    );

    expect(mockedSearchBcsCandidates).toHaveBeenCalledWith('assistant:owner-7', { q: '规划', purpose: 'discovery' }, signal);
    expect(mockedBackendCatalogDiscovery).not.toHaveBeenCalled();
    expect(mockedBackendCatalogSearch).not.toHaveBeenCalled();
    expect(result.searchMode).toBe('semantic');
    expect(result.items[0]).toEqual(
      expect.objectContaining({
        id: 'assistant:owner-8',
        name: '规划助手',
        score: 0.87,
        shortProfile: '擅长任务规划与分解',
        bcsStatus: 'online',
      }),
    );
    // 关系回填走 BCS connections + pending（此处 mock 为空页）。
    expect(mockedListFriendConnections).toHaveBeenCalledWith({ actor_type: 'bot', actor_id: 'assistant:owner-7' }, signal);
  });

  it('Human 视角智能搜索经 resolveHumanBcsActorId 生成 human_<id> Actor', async () => {
    setPublicBotBackendEnabled(false);
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([], 'semantic'));
    mockedListFriendConnections.mockResolvedValue({ code: 20000 as const, message: 'ok', data: { items: [], total: 0 } });
    mockEmptySentPendingRequests();

    await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(mockedSearchBcsCandidates).toHaveBeenCalledWith('human_900003', { q: '规划', purpose: 'discovery' }, undefined);
  });

  it('空关键词本地短路：返回 empty_query 模式，不发任何 BCS/关系请求', async () => {
    setPublicBotBackendEnabled(true);

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '' },
      humanContext,
    );

    expect(result).toEqual({ items: [], searchMode: 'empty_query' });
    expect(mockedSearchBcsCandidates).not.toHaveBeenCalled();
    expect(mockedListFriendConnections).not.toHaveBeenCalled();
    expect(mockedListFriendConnectionRequests).not.toHaveBeenCalled();
    expect(mockedListBotMetadata).not.toHaveBeenCalled();
  });

  it('name_fallback 原样保留 search_mode（不伪造成 semantic），items 无 score', async () => {
    setPublicBotBackendEnabled(false);
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([candidateAt(1)], 'name_fallback'));
    mockedListFriendConnections.mockResolvedValue({ code: 20000 as const, message: 'ok', data: { items: [], total: 0 } });
    mockEmptySentPendingRequests();

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(result.searchMode).toBe('name_fallback');
    expect(result.items[0].score).toBeUndefined();
    expect(result.items[0].shortProfile).toBeUndefined();
  });

  it('同一批候选的 service,false 在普通列表与 semantic/name_fallback 两种智能结果中均被过滤，native Bot 保留', async () => {
    setPublicBotBackendEnabled(true);
    const semanticItems = [
      { ...candidateAt(1, { bot_id: 'svc:owner-1' }), score: 0.9 },
      { ...candidateAt(2, { bot_id: 'ok:owner-2' }), score: 0.8, short_profile: '推荐理由' },
      candidateAt(3, { bot_id: 'native-bot-3' }),
    ];
    mockBotMetadata(new Set(['svc:owner-1']));
    mockBcsCandidatesPage(semanticItems.length, {
      candidateAt: (rawIndex) => semanticItems[rawIndex % semanticItems.length],
    });
    emptyFriendRequestsForDiscovery();

    const adapter = new CollaborationSquareApiAdapter();
    // 普通列表：service,false 被滤除，native Bot 保留。
    const listed = await adapter.listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );
    expect(idsOf(listed.items)).toEqual(['ok:owner-2', 'native-bot-3']);

    // semantic：同一过滤口径，保留原顺序与 score/short_profile。
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope(semanticItems, 'semantic'));
    const semantic = await adapter.discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );
    expect(idsOf(semantic.items)).toEqual(['ok:owner-2', 'native-bot-3']);
    expect(semantic.searchMode).toBe('semantic');
    expect(semantic.items[0].score).toBe(0.8);
    expect(semantic.items[0].shortProfile).toBe('推荐理由');
    expect(semantic.items[1].score).toBeUndefined();

    // name_fallback：同样的过滤、无分数。
    const fallbackItems = [candidateAt(1, { bot_id: 'svc:owner-1' }), candidateAt(2, { bot_id: 'ok:owner-2' }), candidateAt(3, { bot_id: 'native-bot-3' })];
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope(fallbackItems, 'name_fallback'));
    const fallback = await adapter.discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );
    expect(idsOf(fallback.items)).toEqual(['ok:owner-2', 'native-bot-3']);
    expect(fallback.searchMode).toBe('name_fallback');
  });

  it('开启 Backend 最多查询本批 20 条的 metadata、不补页；全部被过滤仍保留原 search_mode 与空批次', async () => {
    setPublicBotBackendEnabled(true);
    const allServiceFalse = Array.from({ length: 20 }, (_, index) => ({
      ...candidateAt(index, { bot_id: `svc:owner-${index}` }),
      score: Number((0.95 - index * 0.01).toFixed(2)),
      short_profile: `语义推荐 ${index}`,
    }));
    mockBotMetadata(new Set(allServiceFalse.map((_, index) => `svc:owner-${index}`)));
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope(allServiceFalse, 'semantic'));
    emptyFriendRequestsForDiscovery();

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '服务', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    // 全部候选被过滤为空：不补页、不再触发 BCS 搜索，仍保留 semantic 模式。
    expect(result.items).toEqual([]);
    expect(result.searchMode).toBe('semantic');
    expect(mockedSearchBcsCandidates).toHaveBeenCalledTimes(1);
    expect(mockedListBotMetadata).toHaveBeenCalledTimes(1);
    const [, body] = mockedListBotMetadata.mock.calls[0];
    expect((body as BotMetadataQueryBody).bots).toHaveLength(20);
  });

  it('关闭 Backend 时智能结果 0 次 metadata 请求；关系回填从 BCS connections 与 pending 完成', async () => {
    setPublicBotBackendEnabled(false);
    mockedSearchBcsCandidates.mockResolvedValueOnce(
      searchEnvelope(
        [
          candidateAt(0, { bot_id: 'planner:owner-8', is_friend: false }),
          candidateAt(1, { bot_id: 'applying:owner-9', is_friend: false }),
          candidateAt(2, { bot_id: 'fresh:owner-10', is_friend: false }),
        ],
        'semantic',
      ),
    );
    mockedListFriendConnections.mockResolvedValue({
      code: 20000 as const,
      message: 'ok',
      data: { items: [{ actor: { type: 'bot', id: 'planner:owner-8' }, is_online: false }], total: 1 },
    });
    mockedListFriendConnectionRequests.mockResolvedValue({
      code: 20000 as const,
      message: 'ok',
      data: {
        items: [
          {
            request_id: 'request-1',
            from_actor: { type: 'human', id: '900003' },
            to_actor: { type: 'bot', id: 'applying:owner-9' },
            status: 'pending',
          },
        ],
        total: 1,
      },
    });

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(mockedListBotMetadata).not.toHaveBeenCalled();
    expect(result.items.map((bot) => [bot.id, bot.relationshipStatus])).toEqual([
      ['planner:owner-8', 'friend'],
      ['applying:owner-9', 'applying'],
      ['fresh:owner-10', 'none'],
    ]);
    // 分数/短画像虽此项无值，但过滤与回填均不改写 bcsStatus。
    expect(result.items.every((bot) => bot.bcsStatus === 'online')).toBe(true);
  });

  it('metadata 401/AceLogin 沿既有鉴权路径，不降级为增强不可用', async () => {
    setPublicBotBackendEnabled(true);
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([candidateAt(0)], 'semantic'));
    mockedListBotMetadata.mockRejectedValueOnce(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/bots/metadata/queries' }),
    );

    await expect(
      new CollaborationSquareApiAdapter().discoverBots(
        { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });

    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([candidateAt(0)], 'semantic'));
    mockedListBotMetadata.mockRejectedValueOnce(new AceLoginRedirectError());
    await expect(
      new CollaborationSquareApiAdapter().discoverBots(
        { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });
    expect(useErrorNotifyStore.getState().drain()).toEqual([]);
  });

  it('metadata 网络/5xx 保留 BCS 结果并提示增强不可用，search_mode 不丢', async () => {
    setPublicBotBackendEnabled(true);
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([candidateAt(0)], 'name_fallback'));
    mockedListBotMetadata.mockRejectedValueOnce(
      new BackendRequestError('服务暂不可用', { status: 503, apiPath: '/openapi/v1/bots/metadata/queries' }),
    );
    emptyFriendRequestsForDiscovery();

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(idsOf(result.items)).toEqual(['assistant:owner-0']);
    expect(result.searchMode).toBe('name_fallback');
    // Service 层只 enqueue（不直调 toast）：观察者按 id=toastKey 投递同款文案并去重合并。
    const drained = useErrorNotifyStore.getState().drain();
    expect(drained).toHaveLength(1);
    expect(drained[0].toastKey).toBe('collaboration-square-bot-metadata-unavailable');
    expect(drained[0].message).toBe('Bot 展示增强暂不可用，已展示基础信息');
    expect(drained[0].cancelled).toBeUndefined();
  });

  it('AbortError 原样传播（搜索或 metadata 均不吞）', async () => {
    setPublicBotBackendEnabled(false);
    const searchAbort = new DOMException('aborted', 'AbortError');
    mockedSearchBcsCandidates.mockRejectedValueOnce(searchAbort);
    await expect(
      new CollaborationSquareApiAdapter().discoverBots(
        { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toBe(searchAbort);

    setPublicBotBackendEnabled(true);
    mockedSearchBcsCandidates.mockResolvedValueOnce(searchEnvelope([candidateAt(0)], 'semantic'));
    const metadataAbort = new DOMException('aborted', 'AbortError');
    mockedListBotMetadata.mockRejectedValueOnce(metadataAbort);
    await expect(
      new CollaborationSquareApiAdapter().discoverBots(
        { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toBe(metadataAbort);
  });

  it('BCS 401/搜索废止等传输失败沿既有错误映射', async () => {
    setPublicBotBackendEnabled(false);
    mockedSearchBcsCandidates.mockRejectedValueOnce(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/api/v1/collaboration/bots/human_900003/candidates/search' }),
    );
    await expect(
      new CollaborationSquareApiAdapter().discoverBots(
        { keyword: '规划', viewerActorType: 'human', viewerActorId: '900003' },
        humanContext,
      ),
    ).rejects.toMatchObject({ code: 'unauthenticated' });
  });
});
