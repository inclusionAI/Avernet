/** @jest-environment jsdom */
// BCS 公开 Bot 候选共用的 Backend metadata 关联/服务发布过滤
// （bcs-standalone-friend-discovery Task 2）。普通列表、智能语义与名称回退共用本过滤；
// Task 3 将由 CollaborationSquareApiAdapter 接入真实 Controller 与信封检查。
import type { PublicBot } from '@/domain/collaborationSquare/types';
import type { BotMetadataDto, BotMetadataQueryItem } from '@/services/backendApi/bots/botController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import {
  BOT_METADATA_QUERY_CHUNK_SIZE,
  filterPublicBotsByMetadata,
  type FetchPublicBotMetadata,
} from '@/services/collaborationSquare/publicBotMetadata';

/** 智能结果的泛型附带字段（score/shortProfile）与普通列表的 bcsStatus 必须原样保留。 */
interface ScoredPublicBot extends PublicBot {
  score?: number;
  shortProfile?: string;
}

function compoundBot(index: number, overrides: Partial<ScoredPublicBot> = {}): ScoredPublicBot {
  return {
    id: `assistant:owner-${index}`,
    name: `候选Bot${index}`,
    ownerName: '未公开',
    description: `第 ${index} 条摘要`,
    capabilities: [],
    relationshipStatus: 'none',
    ...overrides,
  };
}

function metadataRecord(pair: { bot_id: string; owner_id: string }, overrides: Partial<BotMetadataDto> = {}): BotMetadataDto {
  return {
    bcn_bot_id: `${pair.bot_id}:${pair.owner_id}`,
    bot_id: pair.bot_id,
    owner_id: pair.owner_id,
    bot_name: `候选Bot`,
    ...overrides,
  };
}

function authError(status: 401 | 403 | 500): BackendRequestError {
  return new BackendRequestError('服务不可用', { status, apiPath: '/openapi/v1/bots/metadata/queries' });
}

describe('filterPublicBotsByMetadata', () => {
  const signal = new AbortController().signal;
  const fetchMetadata = jest.fn<ReturnType<FetchPublicBotMetadata>, Parameters<FetchPublicBotMetadata>>();
  const onEnhancementUnavailable = jest.fn();

  beforeEach(() => {
    fetchMetadata.mockReset();
    onEnhancementUnavailable.mockClear();
    useErrorNotifyStore.getState().reset();
  });

  it('匹配到 metadata 的候选回填 owner_name，未匹配候选保持「未公开」', async () => {
    const matched = compoundBot(0);
    const unmatched = compoundBot(1);
    fetchMetadata.mockResolvedValue([
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-0' }, { owner_name: '张三' }),
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots: [matched, unmatched],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(kept[0].ownerName).toBe('张三');
    expect(kept[1].ownerName).toBe('未公开');
    // 无 owner_name 可回填的候选保留原对象引用（其余字段原样）。
    expect(kept[1]).toBe(unmatched);
  });

  it('owner_name 空白时保持「未公开」并保留原对象引用', async () => {
    const bot = compoundBot(0);
    fetchMetadata.mockResolvedValue([
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-0' }, { owner_name: '  ' }),
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots: [bot],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(kept[0].ownerName).toBe('未公开');
    expect(kept[0]).toBe(bot);
  });

  it('bcn_bot_id 与 bot_id:owner_id 不一致时按 bcn 稳定引用回填 owner_name', async () => {
    const bot = { ...compoundBot(0), id: 'botA:provider-ref' };
    fetchMetadata.mockResolvedValue([
      metadataRecord(
        { bot_id: 'botA', owner_id: '900003' },
        { bcn_bot_id: 'botA:provider-ref', owner_name: '李四' },
      ),
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots: [bot],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(kept[0].ownerName).toBe('李四');
  });

  it('backendEnabled=false 时不发任何 metadata 请求，原样保留整批候选', async () => {
    const bots = [compoundBot(0), compoundBot(1), compoundBot(2)];

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: false,
      fetchMetadata,
      signal,
    });

    expect(kept).toBe(bots);
    expect(fetchMetadata).not.toHaveBeenCalled();
  });

  it('空批次直接返回，不发请求', async () => {
    const kept = await filterPublicBotsByMetadata({
      bots: [],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });
    expect(kept).toEqual([]);
    expect(fetchMetadata).not.toHaveBeenCalled();
  });

  it('按登录 Human 的 user_id 查询身份对；按 bcn_bot_id 匹配乱序响应，保留顺序/泛型字段/bcsStatus/分数/短画像', async () => {
    // 候选：0=service true 保留；1=service false 拆除；2=personal online_available:null 保留；
    // 3=service null 拆除；4=Backend 无记录 保留；bare=裸 UUID（无可解析身份对）不参与查询。
    const keptServiceTrue = compoundBot(0, { bcsStatus: 'online', score: 0.92, shortProfile: '推荐语-0' });
    const droppedServiceFalse = compoundBot(1, { bcsStatus: 'online', score: 0.9, shortProfile: '推荐语-1' });
    const keptPersonalNull = compoundBot(2, { bcsStatus: 'hidden' });
    const droppedServiceNull = compoundBot(3, { bcsStatus: 'online' });
    const keptUnknownRecord = compoundBot(4, { bcsStatus: 'online' });
    // bcn_bot_id 缺失的 service false 记录：按 bot_id:owner_id 复合引用匹配（同款人机）。
    const droppedNoBcnReference = { ...compoundBot(5, { bcsStatus: 'online' }), id: 'assistant:owner-5' };
    const keptNativeBareUuid: ScoredPublicBot = {
      ...compoundBot(6, { bcsStatus: 'online' }),
      id: 'bcn-native-uuid-6',
    };
    const bots = [
      keptServiceTrue,
      droppedServiceFalse,
      keptPersonalNull,
      droppedServiceNull,
      keptUnknownRecord,
      droppedNoBcnReference,
      keptNativeBareUuid,
    ];
    // 乱序响应：故意打乱 + 缺失 owner-4 的记录 + owner-5 无 bcn_bot_id。
    fetchMetadata.mockResolvedValue([
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-2' }, { bot_type: 'personal', online_available: null }),
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-0' }, { bot_type: 'service', online_available: true }),
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-3' }, { bot_type: 'service', online_available: null }),
      metadataRecord({ bot_id: 'assistant', owner_id: 'owner-1' }, { bot_type: 'service', online_available: false }),
      { bot_id: 'assistant', owner_id: 'owner-5', bot_name: '候选Bot', bot_type: 'service', online_available: false },
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    // 每批精确 Bot/Owner 身份对：只包含可解析的复合引用（6 个 compound 里扣掉裸 UUID）。
    expect(fetchMetadata).toHaveBeenCalledTimes(1);
    const [userIdArg, pairsArg, signalArg] = fetchMetadata.mock.calls[0];
    expect(userIdArg).toBe('7');
    expect(pairsArg).toEqual([
      { bot_id: 'assistant', owner_id: 'owner-0' },
      { bot_id: 'assistant', owner_id: 'owner-1' },
      { bot_id: 'assistant', owner_id: 'owner-2' },
      { bot_id: 'assistant', owner_id: 'owner-3' },
      { bot_id: 'assistant', owner_id: 'owner-4' },
      { bot_id: 'assistant', owner_id: 'owner-5' },
    ]);
    expect(signalArg).toBe(signal);

    // BCS 原顺序保留；service false / service null 拆除；unknown、非 service、service true、
    // 裸 UUID 原生 Bot 全部保留；保留对象即输入引用（bcsStatus/推荐分数/短画像不被改写）。
    expect(kept).toEqual([keptServiceTrue, keptPersonalNull, keptUnknownRecord, keptNativeBareUuid]);
    expect(kept).toContain(keptServiceTrue);
    expect(kept).toContain(keptNativeBareUuid);
    expect(onEnhancementUnavailable).not.toHaveBeenCalled();
  });

  it('无可解析的身份对（全部裸 UUID）时不发请求，全部保留', async () => {
    const bots: ScoredPublicBot[] = [
      { ...compoundBot(0), id: 'bcn-uuid-0' },
      { ...compoundBot(1), id: 'bcn-uuid-1' },
    ];

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(kept).toBe(bots);
    expect(fetchMetadata).not.toHaveBeenCalled();
  });

  it('超过 100 对时按每批 ≤100 组分批查询', async () => {
    const bots = Array.from({ length: BOT_METADATA_QUERY_CHUNK_SIZE * 2 + 50 }, (_, index) => compoundBot(index));
    fetchMetadata.mockImplementation(async (_userId: string, pairs: BotMetadataQueryItem[]) =>
      pairs.map((pair) =>
        metadataRecord(pair, { bot_type: pair.owner_id === 'owner-1' ? 'service' : 'personal', online_available: false }),
      ),
    );

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(fetchMetadata).toHaveBeenCalledTimes(3);
    expect(fetchMetadata.mock.calls[0][1]).toHaveLength(BOT_METADATA_QUERY_CHUNK_SIZE);
    expect(fetchMetadata.mock.calls[1][1]).toHaveLength(BOT_METADATA_QUERY_CHUNK_SIZE);
    expect(fetchMetadata.mock.calls[2][1]).toHaveLength(50);
    // 唯一 service:false 候选被拆除，其余全保留。
    expect(kept).toHaveLength(bots.length - 1);
  });

  it('网络/5xx 失败保留全部 BCS 候选并经回调提示增强不可用', async () => {
    const bots = [compoundBot(0), compoundBot(1)];
    fetchMetadata.mockRejectedValue(authError(500));

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    expect(kept).toBe(bots);
    expect(onEnhancementUnavailable).toHaveBeenCalledTimes(1);
  });

  it('5xx 降级前取消 transport 层已入队的默认提示（toastKey），不再叠加双 toast', async () => {
    const bots = [compoundBot(0), compoundBot(1)];
    const toastKey = 'notify:bots-metadata-queries-500';
    // httpClient 在上抛 BackendRequestError 前已 enqueue 默认失败提示（error.toastKey）。
    useErrorNotifyStore.getState().enqueue({
      toastKey,
      message: 'Bot metadata 服务不可用',
      apiPath: '/openapi/v1/bots/metadata/queries',
    });
    fetchMetadata.mockRejectedValue(
      new BackendRequestError('服务不可用', { status: 500, apiPath: '/openapi/v1/bots/metadata/queries', toastKey }),
    );

    const kept = await filterPublicBotsByMetadata({
      bots,
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
      onEnhancementUnavailable,
    });

    expect(kept).toBe(bots);
    expect(onEnhancementUnavailable).toHaveBeenCalledTimes(1);
    const drained = useErrorNotifyStore.getState().drain();
    // 默认失败提示被取消：观察者 drain 后跳过，用户只看到「增强不可用」降级提示。
    expect(drained).toHaveLength(1);
    expect(drained[0].cancelled).toBe(true);
  });

  it.each([401, 403] as const)('Backend %d 沿认证错误路径原样上抛，不降级为「增强不可用」', async (status) => {
    const bots = [compoundBot(0)];
    const error = authError(status);
    fetchMetadata.mockRejectedValue(error);

    await expect(
      filterPublicBotsByMetadata({
        bots,
        userId: '7',
        backendEnabled: true,
        fetchMetadata,
        signal,
        onEnhancementUnavailable,
      }),
    ).rejects.toBe(error);
    expect(onEnhancementUnavailable).not.toHaveBeenCalled();
  });

  it('AbortError 原样传播', async () => {
    const bots = [compoundBot(0)];
    const abortError = new DOMException('The operation was aborted.', 'AbortError');
    fetchMetadata.mockRejectedValue(abortError);

    await expect(
      filterPublicBotsByMetadata({
        bots,
        userId: '7',
        backendEnabled: true,
        fetchMetadata,
        signal,
        onEnhancementUnavailable,
      }),
    ).rejects.toBe(abortError);
    expect(onEnhancementUnavailable).not.toHaveBeenCalled();
  });

  it('bcn_bot_id ≠ bot_id:owner_id 时按 bcn_bot_id 优先匹配并过滤复合候选', async () => {
    const candidate = { ...compoundBot(0), id: 'botA:provider-ref' };
    fetchMetadata.mockResolvedValue([
      {
        bcn_bot_id: 'botA:provider-ref',
        bot_id: 'botA',
        owner_id: '900003',
        bot_name: '候选Bot',
        bot_type: 'service',
        online_available: false,
      },
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots: [candidate],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    // 只按 bot_id:owner_id 匹配会把它当作「无记录」误保留。
    expect(kept).toEqual([]);
  });

  it('owner 交接后的旧复合引用（bcn_bot_id 不同）不误伤当前候选', async () => {
    const candidate = { ...compoundBot(0), id: 'botA:900003' };
    fetchMetadata.mockResolvedValue([
      {
        bcn_bot_id: 'botA:old-owner',
        bot_id: 'botA',
        owner_id: '900003',
        bot_name: '候选Bot',
        bot_type: 'service',
        online_available: false,
      },
    ]);

    const kept = await filterPublicBotsByMetadata({
      bots: [candidate],
      userId: '7',
      backendEnabled: true,
      fetchMetadata,
      signal,
    });

    expect(kept).toEqual([candidate]);
  });
});
