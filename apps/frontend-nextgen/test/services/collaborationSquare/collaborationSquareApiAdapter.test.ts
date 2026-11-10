/** @jest-environment jsdom */
// bcs-standalone-friend-discovery Task 3：发现页普通列表/智能搜索/画像全部改走 BCS v1。
// 普通列表及名称过滤必须调用 BCS `candidates`（经 publicBotCursor 扫描，上游每批
// ≤BCS_CANDIDATE_SCAN_PAGE_SIZE），智能模式调用内部 `candidates/search` 并保留
// search_mode/score/short_profile；Backend 只承担可选 metadata 过滤与画像增强，
// 且认证类失败（401/403 或 oauth-provider 的 AceLoginRedirectError）必须沿既有
// 鉴权路径上抛——不得被降级为「增强不可用」。
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import * as bbsTaskController from '@/services/backendApi/collaboration/bbsTaskController';
import * as collaborationBotController from '@/services/backendApi/collaboration/collaborationBotController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as publicBotController from '@/services/backendApi/collaboration/publicBotController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import * as botSessionController from '@/services/backendApi/bots/privateBotSessionController';
import * as backendBotController from '@/services/backendApi/bots/botController';
import * as bcsCandidatesModule from '@/services/bcs/botCandidatesController';
import { AceLoginRedirectError, BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';
import { BCS_CANDIDATE_SCAN_PAGE_SIZE } from '@/services/collaborationSquare/publicBotCursor';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import type { BotMetadataDto, BotMetadataQueryBody } from '@/services/backendApi/bots/botController';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// 注意：本文件使用 @jest/globals 名义 import（既有约定），与 babel 的 jest.mock 工厂提升
// 不兼容（工厂执行早于命名绑定初始化），故对依赖模块统一采用属性 spy 实现同态 mock。
const mockedListBcsBotCandidates = jest.spyOn(bcsCandidatesModule, 'listBcsBotCandidates');
const mockedSearchBcsCandidates = jest.spyOn(bcsCandidatesModule, 'searchBcsCandidates');
const mockedListBotMetadata = jest.spyOn(backendBotController, 'listBotMetadata');
const mockedBackendCatalogSearch = jest.spyOn(publicBotController, 'searchPublicBots');
const mockedBackendCatalogDiscovery = jest.spyOn(publicBotController, 'discoverPublicBots');
const mockedGetPublicBotProfile = jest.spyOn(publicBotController, 'getPublicBotProfile');
const mockedListPublicGroups = jest.spyOn(groupController, 'listPublicGroups');
const mockedQueryCollaborationBots = jest.spyOn(collaborationBotController, 'queryCollaborationBots');
const mockedListFriendConnections = jest.spyOn(friendConnectionController, 'listFriendConnections');
const mockedListFriendConnectionRequests = jest.spyOn(friendConnectionController, 'listFriendConnectionRequests');
const mockedCreateFriendConnectionRequest = jest.spyOn(friendConnectionController, 'createFriendConnectionRequest');
const mockedCreateBotSession = jest.spyOn(botSessionController, 'createBotSession');
const mockedCreateGroupSession = jest.spyOn(sessionController, 'createSession');
const mockedListBbsTasks = jest.spyOn(bbsTaskController, 'listBbsTasks');

const humanContext = { actorId: 'human_900003', userId: '900003' };

/** 测试内显式开关 Backend 可选增强（defaultCapabilities 读取 window.AVERNET_RUNTIME_CONFIG）。 */
function setPublicBotBackendEnabled(value: boolean) {
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    value: { PUBLIC_BOT_BACKEND_ENABLED: value },
    configurable: true,
  });
}

interface CandidateFixtureOptions {
  bot_id?: string;
  name?: string;
  status?: 'online' | 'hidden';
  is_friend?: boolean;
}

/** 以 BCS 契约形状构造候选项（对齐 domain-models.yaml BotCandidate/PhysicalBot 消费字段）。 */
function candidateAt(rawIndex: number, options: CandidateFixtureOptions = {}) {
  return {
    bot: {
      bot_id: options.bot_id ?? `assistant:owner-${rawIndex}`,
      name: options.name ?? `候选Bot${rawIndex}`,
      avatar_url: null,
      created_by: '9',
      descriptor: { summary: `第 ${rawIndex} 条候选摘要` },
      visibility: 'public' as const,
      user_visibility: 'public' as const,
      status: options.status ?? ('online' as const),
    },
    is_friend: options.is_friend ?? false,
  };
}

/** 以 BCS 原始 offset 提供候选页（upstream 固定 100 一批）。 */
function mockBcsCandidatesPage(
  total: number,
  options: { candidateAt?: (rawIndex: number) => ReturnType<typeof candidateAt> } = {},
) {
  const buildCandidate = options.candidateAt ?? candidateAt;
  mockedListBcsBotCandidates.mockImplementation(
    async (_actorId: string, query: { offset: number; limit: number }) => {
    const count = Math.max(0, Math.min(query.limit, total - query.offset));
    return {
      code: 20000 as const,
      message: 'ok',
      data: {
        items: Array.from({ length: count }, (_, index) => buildCandidate(query.offset + index)),
        total,
        offset: query.offset,
        limit: query.limit,
      },
    };
  });
}

/** 模拟 metadata/queries：serviceFalseKeys（`bot_id:owner_id` 形态）标记为 service+false。 */
function mockBotMetadata(serviceFalseKeys: Set<string>) {
  mockedListBotMetadata.mockImplementation(async (_params: unknown, body: BotMetadataQueryBody) => {
    const items: BotMetadataDto[] = body.bots.map((pair) => {
      const key = `${pair.bot_id}:${pair.owner_id}`;
      const serviceFalse = serviceFalseKeys.has(key);
      return {
        bcn_bot_id: key,
        bot_id: pair.bot_id,
        owner_id: pair.owner_id,
        bot_name: key,
        bot_type: serviceFalse ? 'service' : 'personal',
        online_available: serviceFalse ? false : null,
      };
    });
    return { code: 200000 as const, message: 'ok', data: { items, total: items.length, offset: 0, limit: items.length } };
  });
}

const emptyFriendEnvelope = { code: 20000 as const, message: 'ok', data: { items: [], total: 0 } };
/** 列表路径的默认申请侧供给：无 pending 申请。 */
function mockEmptySentPendingRequests() {
  mockedListFriendConnectionRequests.mockResolvedValue(emptyFriendEnvelope);
}

/** 智能搜索关系回填的空 BCS 页（connections + pending 均为空页）。 */
function emptyFriendRequestsForDiscovery() {
  mockedListFriendConnections.mockResolvedValue(emptyFriendEnvelope);
  mockedListFriendConnectionRequests.mockResolvedValue(emptyFriendEnvelope);
}

function idsOf(bots: PublicBot[]) {
  return bots.map((bot) => bot.id);
}

beforeEach(() => {
  mockedListBcsBotCandidates.mockReset();
  mockedSearchBcsCandidates.mockReset();
  mockedListBotMetadata.mockReset();
  mockedBackendCatalogSearch.mockReset();
  mockedBackendCatalogDiscovery.mockReset();
  mockedGetPublicBotProfile.mockReset();
  mockedListPublicGroups.mockReset();
  mockedQueryCollaborationBots.mockReset();
  mockedListFriendConnections.mockReset();
  mockedListFriendConnectionRequests.mockReset();
  mockedCreateFriendConnectionRequest.mockReset();
  mockedCreateBotSession.mockReset();
  mockedCreateGroupSession.mockReset();
  mockedListBbsTasks.mockReset();
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
  useErrorNotifyStore.getState().reset();
});

afterEach(() => {
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
});

describe('CollaborationSquareApiAdapter listBotPage（BCS v1 公开候选）', () => {
  it('Human 视角生成 human_<user-id> Actor、上游整批 ≤100：urpose=discovery 交给 Controller 组装', async () => {
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
    expect(mockedListBotMetadata).toHaveBeenCalledTimes(1);
    const [metadataParams, metadataBody, metadataSignal] = mockedListBotMetadata.mock.calls[0];
    // metadata 显式带登录 Human 的 user_id，page_size 覆盖整批身份对（native bot 无冒号，29 对）。
    expect(metadataParams).toMatchObject({ user_id: '900003', page_size: 29 });
    const pairs = (metadataBody as BotMetadataQueryBody).bots;
    expect(pairs).toHaveLength(29);
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

  it('BCS 候选映射把 created_by 匹配登录 userId 的项标记为自有 Bot', async () => {
    setPublicBotBackendEnabled(false);
    mockBcsCandidatesPage(1);
    mockEmptySentPendingRequests();

    const page = await new CollaborationSquareApiAdapter().listBotPage(
      { offset: 0, pageSize: 24, viewerActorType: 'human', viewerActorId: '900003' },
      humanContext,
    );

    expect(page.items[0].isOwnedByLoggedInUser).toBeUndefined();
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

describe('CollaborationSquareApiAdapter getBotProfile（BCS 摘要 + 可选 Backend 画像增强）', () => {
  const candidateBot: PublicBot = {
    id: 'assistant:owner-8',
    name: '规划助手',
    ownerName: '未公开',
    description: '擅长任务规划与分解',
    capabilities: [],
    relationshipStatus: 'none',
    bcsStatus: 'hidden',
    shortProfile: '语义短画像',
  };

  it('Backend 未配置时直接从所点候选项构建 {capabilities:[],skills:[]} 摘要，不读旧 profile', async () => {
    setPublicBotBackendEnabled(false);

    const profile = await new CollaborationSquareApiAdapter().getBotProfile(candidateBot);

    expect(profile).toEqual({
      id: 'assistant:owner-8',
      name: '规划助手',
      ownerName: '未公开',
      description: '擅长任务规划与分解',
      shortProfile: '语义短画像',
      bcsStatus: 'hidden',
      capabilities: [],
      skills: [],
    });
    expect(mockedGetPublicBotProfile).not.toHaveBeenCalled();
  });

  it('Backend 启用时读取旧 profile 补齐引擎/已公开能力，且不被 Backend 生命周期覆盖 bcsStatus', async () => {
    setPublicBotBackendEnabled(true);
    mockedGetPublicBotProfile.mockResolvedValueOnce({
      code: 200000 as const,
      message: 'ok',
      data: {
        bot_id: 'assistant:owner-8',
        bot_name: '规划助手',
        owner_name: '风太',
        description: null,
        engine_name: 'OpenClaw',
        capabilities: [{ capability_id: 'antv', display_name: 'AntV 可视化图表' }],
        skills: [{ skill_id: 'weekly-report', display_name: '风太周报' }],
      },
    });
    const signal = new AbortController().signal;

    const profile = await new CollaborationSquareApiAdapter().getBotProfile(candidateBot, signal);

    expect(mockedGetPublicBotProfile).toHaveBeenCalledWith('assistant:owner-8', signal);
    expect(profile).toEqual({
      id: 'assistant:owner-8',
      name: '规划助手',
      ownerName: '风太',
      // Backend 描述缺省时保留 BCS 摘要描述。
      description: '擅长任务规划与分解',
      engine: 'OpenClaw',
      capabilities: [{ id: 'antv', name: 'AntV 可视化图表' }],
      skills: [{ id: 'weekly-report', name: '风太周报' }],
      shortProfile: '语义短画像',
      // 候选的 bcsStatus 原样保留，不被 Backend Catalog 生命周期 status 覆盖（也不伪造）。
      bcsStatus: 'hidden',
    });
    expect(profile.status).toBeUndefined();
  });

  it('画像增强失败（404/5xx）回退 BCS 摘要且取消协议层默认提示', async () => {
    setPublicBotBackendEnabled(true);
    const toastKey = 'notify:bots-catalog-profile-404';
    useErrorNotifyStore.getState().enqueue({
      toastKey,
      message: 'Public bot profile not found',
      apiPath: '/openapi/v1/bots/catalog/profile/assistant:owner-8',
    });
    mockedGetPublicBotProfile.mockRejectedValueOnce(
      new BackendRequestError('not found', {
        status: 404,
        apiPath: '/openapi/v1/bots/catalog/profile/assistant:owner-8',
        toastKey,
      }),
    );

    const profile = await new CollaborationSquareApiAdapter().getBotProfile(candidateBot);

    expect(profile).toEqual(expect.objectContaining({ id: 'assistant:owner-8', capabilities: [], skills: [], bcsStatus: 'hidden' }));
    const drained = useErrorNotifyStore.getState().drain();
    expect(drained).toHaveLength(1);
    // 协议层已入队的默认 sonner 投递被取消：降级不再叠加 toast。
    expect(drained[0].cancelled).toBe(true);
    useErrorNotifyStore.getState().reset();
  });

  it('画像增强鉴权失败仍沿既有鉴权路径，不静默回退摘要', async () => {
    setPublicBotBackendEnabled(true);
    mockedGetPublicBotProfile.mockRejectedValueOnce(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/bots/catalog/profile/assistant:owner-8' }),
    );
    await expect(new CollaborationSquareApiAdapter().getBotProfile(candidateBot)).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    mockedGetPublicBotProfile.mockRejectedValueOnce(new AceLoginRedirectError());
    await expect(new CollaborationSquareApiAdapter().getBotProfile(candidateBot)).rejects.toMatchObject({
      code: 'unauthenticated',
    });
  });

  it('画像读取 AbortError 原样透传', async () => {
    setPublicBotBackendEnabled(true);
    const abortError = new DOMException('aborted', 'AbortError');
    mockedGetPublicBotProfile.mockRejectedValueOnce(abortError);
    await expect(new CollaborationSquareApiAdapter().getBotProfile(candidateBot)).rejects.toBe(abortError);
  });
});

describe('CollaborationSquareApiAdapter 好友申请', () => {
  it.each([
    ['pending', 'applying'],
    ['approved', 'friend'],
    ['public_no_edge', 'none'],
  ] as const)('maps Friend Connection request status %s to %s', async (status, expected) => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status, auto_accepted: status === 'approved' },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).resolves.toEqual({
      status: expected,
    });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'bot-1' },
      from_actor: { type: 'human', id: '900003' },
    });
  });

  it('uses an explicit bot_uuid when the catalog provides one', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship('default', humanContext, 'explicit-bot-uuid'),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'explicit-bot-uuid' },
      from_actor: { type: 'human', id: '900003' },
    });
  });

  it('falls back to the composite bot_id:entity_id target for friend requests', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship('20260410_kt9ermvn', humanContext, '20260410_kt9ermvn:431368'),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: '20260410_kt9ermvn:431368' },
      from_actor: { type: 'human', id: '900003' },
    });
  });

  it('honor an explicit fromActor for friend requests (对话协作按当前角色 tab)', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext, undefined, {
        type: 'bot',
        id: 'bot-xyz',
      }),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'bot-1' },
      from_actor: { type: 'bot', id: 'bot-xyz' },
    });
  });

  it('rejects an unknown Friend Connection request status as a protocol error', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: [], edge_ids: [], status: 'mystery' as never, auto_accepted: false },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('rejects an unexpected Friend Connection request business code', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20000,
      data: { request_ids: [], edge_ids: [], status: 'approved', auto_accepted: true },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('keeps the Bot available when the request endpoint returns bot_not_found', async () => {
    mockedCreateFriendConnectionRequest.mockRejectedValue(
      new BackendRequestError('Bot not found', {
        status: 404,
        data: { code: 40400, data: { error_code: 'bot_not_found' } },
        apiPath: '/openapi/v1/collaboration/friend-connections/requests',
      }),
    );

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject({
      code: 'network',
      message: '目标 Bot 当前不可用，申请未提交，请稍后重试',
    });
  });

  it('creates a private Bot session with split Bot identity and the normalized Human user id', async () => {
    mockedCreateBotSession.mockResolvedValue({
      code: 200000,
      data: {
        session_id: 'session-1',
        title: '新会话',
        agent_id: 'agent-1',
        model: 'model-1',
        message_count: 0,
        gmt_create: '2026-08-22T00:00:00Z',
        gmt_modified: '2026-08-22T00:00:00Z',
      },
    });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1:2088', humanContext)).resolves.toEqual({
      sessionId: 'session-1',
    });
    expect(mockedCreateBotSession).toHaveBeenCalledWith('bot-1', { user_id: '900003', owner_id: '2088', f_user_id: '900003' }, {});
  });

  it('omits f_user_id when the target Bot is owned by the logged-in user', async () => {
    mockedCreateBotSession.mockResolvedValue({
      code: 200000,
      data: { session_id: 'session-owned' } as never,
    });

    await expect(
      new CollaborationSquareApiAdapter().openBotConversation('bot-1:2088', humanContext, {
        isOwnedByLoggedInUser: true,
      }),
    ).resolves.toEqual({ sessionId: 'session-owned' });
    expect(mockedCreateBotSession).toHaveBeenCalledWith('bot-1', { user_id: '900003', owner_id: '2088' }, {});
  });

  it('rejects a session response without session_id as a protocol error', async () => {
    mockedCreateBotSession.mockResolvedValue({ code: 200000, data: {} as never });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('rejects an unexpected private Session business code even when session_id is present', async () => {
    mockedCreateBotSession.mockResolvedValue({ code: 20100, data: { session_id: 'session-1' } as never });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('maps action authentication errors without falling back to Mock', async () => {
    mockedCreateFriendConnectionRequest.mockRejectedValue(
      new BackendRequestError('forbidden', { status: 403, apiPath: '/friend-connections/requests' }),
    );
    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject({
      code: 'forbidden',
    });

    mockedCreateBotSession.mockRejectedValue(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/sessions' }),
    );
    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'unauthenticated',
    });
  });
});

describe('CollaborationSquareApiAdapter 公开群', () => {
  it('maps only public normal groups from the real list response', async () => {
    mockedListPublicGroups.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            group_id: 'group-public-1',
            version: 1,
            kind: 'normal',
            status: 'active',
            visibility: 'public',
            originator_actor_id: 'human-1',
            participants: [],
            driver_bot_uuid: 'bot-1',
            collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
            name: '公开协作群',
            participant_count: 2,
            created_at: 1,
            updated_at: 2,
          },
          {
            group_id: 'group-private-1',
            version: 1,
            kind: 'normal',
            status: 'active',
            visibility: 'private',
            originator_actor_id: 'human-1',
            participants: [],
            driver_bot_uuid: 'bot-1',
            collaboration: { strategy: 'manager_worker' },
            name: '私有群',
            created_at: 1,
            updated_at: 2,
          },
        ],
        total: 2,
      },
    });
    const signal = new AbortController().signal;
    mockedQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: { items: [{ bot_id: 'bot-1', kind: 'bot', name: '主理 Bot' }] },
    });

    const result = await new CollaborationSquareApiAdapter().listGroups(
      { search: '公开', offset: 0, limit: 20 },
      signal,
    );

    expect(mockedListPublicGroups).toHaveBeenCalledWith({ q: '公开', offset: 0, limit: 20 }, signal);
    // 公开群目录无 participants，群主名经 bots/query 按 driver_bot_uuid 反查。
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-1'] });
    expect(result).toEqual([
      expect.objectContaining({
        id: 'group-public-1',
        name: '公开协作群',
        ownerBotName: '主理 Bot',
        ownerUserName: '未公开',
        memberCount: 2,
      }),
    ]);
  });

  it('listGroups: driver bot 查不到时回退展示 uuid，不显示"未公开"', async () => {
    mockedListPublicGroups.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            group_id: 'group-public-1',
            version: 1,
            kind: 'normal',
            status: 'active',
            visibility: 'public',
            originator_actor_id: 'human-1',
            participants: [],
            driver_bot_uuid: 'bot-deleted',
            collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
            name: '公开协作群',
            participant_count: 1,
            created_at: 1,
            updated_at: 1,
          },
        ],
        total: 1,
      },
    });
    // bots/query 返回空（driver bot 已删除/查不到）。
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
    const result = await new CollaborationSquareApiAdapter().listGroups({ offset: 0, limit: 20 });
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-deleted'] });
    expect(result[0].ownerBotName).toBe('bot-deleted');
  });

  it('listGroupMembers 调 getGroup 详情并映射 participants → PublicGroupMember[]', async () => {
    const mockedGetGroup = jest.spyOn(groupController, 'getGroup').mockResolvedValue({
      code: 20000,
      data: {
        group_id: 'group-1',
        version: 1,
        kind: 'normal',
        status: 'active',
        visibility: 'public',
        originator_actor_id: 'human_1',
        participants: [
          { actor_id: 'bot-1', actor_kind: 'bot', name: '主理 Bot', role: 'manager', mode: 'auto' },
          { actor_id: 'human_1', actor_kind: 'human', name: '章梧', role: 'consultant', mode: 'present' },
        ],
        driver_bot_uuid: 'bot-1',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        created_at: 1,
        updated_at: 1,
      },
    });
    const members = await new CollaborationSquareApiAdapter().listGroupMembers('group-1');
    expect(mockedGetGroup).toHaveBeenCalledWith('group-1');
    expect(members).toEqual({
      ownerUserName: '未公开',
      members: [
        { id: 'bot-1', displayName: '主理 Bot', type: 'bot', role: 'manager' },
        { id: 'human_1', displayName: '章梧', type: 'human', role: 'consultant' },
      ],
    });
    mockedGetGroup.mockRestore();
  });

  it('creates a public group session from the authenticated human actor', async () => {
    mockedCreateGroupSession.mockResolvedValue({
      code: 20100,
      message: 'Created',
      data: {
        session_id: 'session-1',
        group_id: 'group-1',
        version: 1,
        status: 'running',
        participants: [
          {
            actor_id: 'human_900003',
            actor_kind: 'human',
            role: 'consultant',
            mode: 'present',
          },
        ],
        created_at: 1,
        updated_at: 1,
      },
      request_id: 'r',
    });

    await expect(new CollaborationSquareApiAdapter().createGroupSession('group-1', humanContext)).resolves.toEqual({
      sessionId: 'session-1',
      defaultRole: '顾问',
      memberSource: 'session_temp',
    });
    expect(mockedCreateGroupSession).toHaveBeenCalledWith('group-1', { kind: 'chat', acting_bot_id: 'human_900003' });
  });

  it('rejects a public group session response without session_id or caller role', async () => {
    mockedCreateGroupSession.mockResolvedValue({
      code: 20100,
      message: 'Created',
      data: {
        session_id: '',
        group_id: 'group-1',
        version: 1,
        status: 'running',
        participants: [],
        created_at: 1,
        updated_at: 1,
      },
      request_id: 'r',
    });

    await expect(new CollaborationSquareApiAdapter().createGroupSession('group-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });
});

describe('listPublicTasks (BBS list 端点)', () => {
  const bbsItem = (
    overrides: Partial<{
      task_id: string;
      title: string;
      goal: string;
      status: string;
      publisher: string | null;
      assignee_id: string;
      assignee_name: string;
      relay_create_time: string;
      relay_begin_time: string;
      relay_end_time: string;
    }> = {},
  ) => ({
    task_id: 'bbs-1',
    title: '梳理需求',
    goal: '输出路线图',
    acceptances: [{ id: 'a1', description: '覆盖方向' }],
    status: 'PENDING',
    publisher: 'bot-pub-1',
    relay_create_time: '2026-09-01T09:00:00Z',
    ...overrides,
  });

  it('映射 BBS envelope（code 200000）到 PublicTask 列表，publisher 经 bots/query 反查', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        total: 2,
        items: [
          bbsItem({ status: 'RUNNING', assignee_id: 'bot-asg', assignee_name: '运维助手', relay_begin_time: 't1' }),
          bbsItem({ task_id: 'bbs-2', status: 'SUCCESS', assignee_id: 'bot-asg', relay_end_time: 't2' }),
        ],
      },
      request_id: 'r',
    });
    // publisher 反查（与群主 driver_bot_uuid 反查同法）。
    mockedQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: { items: [{ bot_id: 'bot-pub-1', kind: 'bot', name: '产品协作助手' }] },
    });

    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

    // 空 query 不下发分页/过滤参数（交服务端默认 page=1, page_size=20）。
    expect(mockedListBbsTasks).toHaveBeenCalledWith({}, undefined);
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-pub-1'] });
    expect(result).toEqual({
      items: [
        {
          id: 'bbs-1',
          name: '梳理需求',
          goal: '输出路线图',
          acceptanceCriteria: ['覆盖方向'],
          status: 'claimed',
          publisher: 'bot-pub-1',
          publisherBotName: '产品协作助手',
          publishedAt: '2026-09-01T09:00:00Z',
          claimedBotName: '运维助手',
          claimedAt: 't1',
        },
        {
          id: 'bbs-2',
          name: '梳理需求',
          goal: '输出路线图',
          acceptanceCriteria: ['覆盖方向'],
          status: 'completed',
          publisher: 'bot-pub-1',
          publisherBotName: '产品协作助手',
          publishedAt: '2026-09-01T09:00:00Z',
          claimedBotName: 'bot-asg',
          claimedAt: undefined,
          completedAt: 't2',
        },
      ],
      total: 2,
    });
  });

  it('DONE 项前向映射为 reviewing 并填完成时间（adapter 级覆盖 DONE→reviewing 分支）', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { total: 1, items: [bbsItem({ task_id: 'bbs-rev', status: 'DONE', relay_end_time: 't3' })] },
      request_id: 'r',
    });
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });

    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

    expect(result.items[0]).toEqual(
      expect.objectContaining({ id: 'bbs-rev', status: 'reviewing', completedAt: 't3' }),
    );
  });

  it('publisher 为复合 bot_id:owner 时拆 realBotId 反查，结果按复合 id 命中（不透传 :owner）', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { total: 1, items: [bbsItem({ task_id: 't-comp', publisher: 'bot-pub-1:431368' })] },
      request_id: 'r',
    });
    // bots/query 注册表按裸 bot_id 建索引，返回的 bot_id 不带 :owner。
    mockedQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: { items: [{ bot_id: 'bot-pub-1', kind: 'bot', name: '产品协作助手' }] },
    });

    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

    // 下发裸 realBotId，不把 :owner 透传进 bots/query。
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-pub-1'] });
    expect(result.items[0].publisherBotName).toBe('产品协作助手');
  });

  it('publisher nameMap 未命中兜底用 ID，publisher 为 null 时 publisherBotName 为 undefined', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        total: 2,
        items: [
          bbsItem({ task_id: 't-miss', publisher: 'bot-missing' }),
          bbsItem({ task_id: 't-null', publisher: null }),
        ],
      },
      request_id: 'r',
    });
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });

    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

    const byId = Object.fromEntries(result.items.map((t) => [t.id, t]));
    expect(byId['t-miss']?.publisherBotName).toBe('bot-missing');
    expect(byId['t-null']?.publisherBotName).toBeUndefined();
  });

  it('无 publisher 时不下发 bots/query 反查', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { total: 1, items: [bbsItem({ task_id: 't-null', publisher: null })] },
      request_id: 'r',
    });
    await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
    expect(mockedQueryCollaborationBots).not.toHaveBeenCalled();
  });

  it('assignee_name 缺失时按 assignee_id 拆 realBotId 反查回填 claimedBotName', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        total: 1,
        items: [
          bbsItem({
            task_id: 't-asg',
            status: 'RUNNING',
            publisher: null,
            assignee_id: 'bot-asg:2088',
            relay_begin_time: 't1',
          }),
        ],
      },
      request_id: 'r',
    });
    mockedQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: { items: [{ bot_id: 'bot-asg', kind: 'bot', name: '运维助手' }] },
    });

    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

    // publisher 为 null 不参与反查；仅 assignee 拆 realBotId 下发。
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-asg'] });
    expect(result.items[0].claimedBotName).toBe('运维助手');
  });

  it('空 data 数组返回空列表且不下发 bots/query', async () => {
    mockedListBbsTasks.mockResolvedValue({ code: 200000, message: 'OK', data: { total: 0, items: [] }, request_id: 'r' });
    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
    expect(result).toEqual({ items: [], total: 0 });
    expect(mockedQueryCollaborationBots).not.toHaveBeenCalled();
  });

  it('未知 status 的 item 被 mapper 丢弃（不入列），total 仍为服务端值', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: {
        total: 3,
        items: [
          bbsItem({ task_id: 'valid', status: 'PENDING' }),
          bbsItem({ task_id: 'drop-cancelled', status: 'CANCELLED' }),
          bbsItem({ task_id: 'drop-failed', status: 'FAILED' }),
        ],
      },
      request_id: 'r',
    });
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
    const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
    expect(result.items.map((t) => t.id)).toEqual(['valid']);
    // total 取自服务端（3），不随 mapper 丢弃未知态而变。
    expect(result.total).toBe(3);
  });

  it('offset/limit 换算为 1-based page/page_size 下发', async () => {
    mockedListBbsTasks.mockResolvedValue({ code: 200000, message: 'OK', data: { total: 0, items: [] }, request_id: 'r' });
    await new CollaborationSquareApiAdapter().listPublicTasks({ offset: 24, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenCalledWith({ page: 2, page_size: 24 }, undefined);
  });

  it('search 换算为 search_word 下发；空/空白不下发', async () => {
    mockedListBbsTasks.mockResolvedValue({ code: 200000, message: 'OK', data: { total: 0, items: [] }, request_id: 'r' });
    await new CollaborationSquareApiAdapter().listPublicTasks({ search: '需求', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenCalledWith({ page: 1, page_size: 24, search_word: '需求' }, undefined);
    // 空白 search 不下发 search_word。
    await new CollaborationSquareApiAdapter().listPublicTasks({ search: '   ', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24 }, undefined);
  });

  it('广场态 status 映射为 BBS 原始态下发；all 不下发 status', async () => {
    mockedListBbsTasks.mockResolvedValue({ code: 200000, message: 'OK', data: { total: 0, items: [] }, request_id: 'r' });
    const adapter = new CollaborationSquareApiAdapter();
    await adapter.listPublicTasks({ status: 'claimed', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'RUNNING' }, undefined);
    await adapter.listPublicTasks({ status: 'reviewing', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'DONE' }, undefined);
    await adapter.listPublicTasks({ status: 'completed', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'SUCCESS' }, undefined);
    await adapter.listPublicTasks({ status: 'pending_claim', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'PENDING' }, undefined);
    // all 不下发 status。
    await adapter.listPublicTasks({ status: 'all', offset: 0, limit: 24 }, undefined);
    expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24 }, undefined);
  });

  it('total 为服务端过滤后行数，不等于本页 mapped 条数（驱动 hasMore）', async () => {
    mockedListBbsTasks.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { total: 100, items: [bbsItem({ task_id: 'a' }), bbsItem({ task_id: 'b' })] },
      request_id: 'r',
    });
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
    const result = await new CollaborationSquareApiAdapter().listPublicTasks({ offset: 0, limit: 24 }, undefined);
    expect(result.items.map((t) => t.id)).toEqual(['a', 'b']);
    expect(result.total).toBe(100);
  });

  it('code != 200000 抛 protocol_error', async () => {
    mockedListBbsTasks.mockResolvedValue({ code: 20000, message: 'NO', data: { total: 0, items: [] } } as never);
    await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('ACE 登录失效映射为 unauthenticated', async () => {
    mockedListBbsTasks.mockResolvedValue({
      actionType: 'LOGIN',
      buserviceErrorCode: 'USER_NOT_LOGIN',
      decisionBy: 'ACE',
      buserviceErrorMsg: 'http://login',
    } as never);
    await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
      code: 'unauthenticated',
    });
  });

  it('AbortError 原样透传，不吞成 network', async () => {
    const abortError = new DOMException('aborted', 'AbortError');
    mockedListBbsTasks.mockRejectedValue(abortError);
    await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toBe(abortError);
  });

  it('BackendRequestError 401 映射为 unauthenticated', async () => {
    mockedListBbsTasks.mockRejectedValue(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/collaboration/tasks/bbs/list' }),
    );
    await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
      code: 'unauthenticated',
    });
  });

  it('getPublicTask 仍为 unsupported（详情改用列表项，不发请求）', async () => {
    await expect(new CollaborationSquareApiAdapter().getPublicTask('bbs-1')).rejects.toMatchObject({
      code: 'unsupported',
    });
  });
});
