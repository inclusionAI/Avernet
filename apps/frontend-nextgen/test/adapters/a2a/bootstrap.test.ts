/**
 * B13 — bootstrap（A2A 传输选择编排）测试：六态决策表钉板 + 同步捷径/预热时序
 * + 运行期锁定接线（端到端）+ §3.1 send-ban 门。
 * ============================================================================
 *
 * 决策表事实源（spec stage2 §2/§3.3）：探测/场景缺项 → probe-v3（可翻案、永不锁）；
 * locked 只来自运行期硬失败（auth/protocol typed）。探测失败细分面
 * （network/timeout/http/bad_json）已在 card.test.ts全覆盖，此处只对决策
 * 收敛路径各取代表面。
 */
import {
  A2A_CHOICE_SESSION_KEY,
  SEND_BAN_UI_TEXT,
  deriveEndpointBase,
  getSendBanRegistry,
  isLockCandidateError,
  isSendBannedForSession,
  maybeBuildA2AProvider,
  resolveA2aUrls,
  takeCachedA2AProvider,
  type A2aBootstrapConfig,
  type A2aBootstrapDeps,
} from '@/adapters/a2a/bootstrap';
import { A2AProvider } from '@/adapters/a2a/A2AProvider';
import { TransportChoiceCache } from '@/adapters/a2a/choiceCache';
import { createSendBanRegistry } from '@/adapters/a2a/sendBan';
import { a2aUrlsFromBaseUrl, a2aUrlsFromWsUrl } from '@/adapters/a2a/urls';
import { makeEngineCard } from './helpers';

// ─── 通用件 ───────────────────────────────────────────────────────────────

const WS_URL = 'ws://127.0.0.1:20003/api/a2a/ws';
const URLS = a2aUrlsFromWsUrl(WS_URL);
const CONFIG: A2aBootstrapConfig = {
  url: WS_URL,
  xIAMToken: 't0k',
  sessionKey: 'sess-1',
  enableThinkingTag: true,
};

const okCardResponse = () => new Response(JSON.stringify(makeEngineCard()), {
  status: 200,
  headers: { 'content-type': 'application/json' },
});

/** 不许走网：兜底失败面 */
const mustNotFetch = async (): Promise<Response> => {
  throw new Error('must not fetch');
};

const expectProbeV3 = (
  cache: TransportChoiceCache,
  endpointBase: string = URLS.rpcUrl,
  sessionKey: string = A2A_CHOICE_SESSION_KEY,
) => expect(cache.get(sessionKey, endpointBase)?.kind).toBe('probe-v3');

// ─── 六态决策表（spec §2/§3.3） ───────────────────────────────────────────

describe('maybeBuildA2AProvider 六态决策表', () => {
  it('① 开关关 → null + 零 fetch + 缓存不动', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => okCardResponse());
    const provider = await maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => false,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(provider).toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)).toBeUndefined();
  });

  it('① 缺省开关（node 无 window）同样 off', async () => {
    expect(await maybeBuildA2AProvider(CONFIG)).toBeNull();
  });

  it('② 探测失败（网络）→ decide(false)=probe-v3 + null（永不锁）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => {
      throw new TypeError('network down');
    });
    const provider = await maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(provider).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expectProbeV3(cache);
  });

  it('② 探测失败（http_500）→ probe-v3（http/timeout 细分同面，card.test 覆盖）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(
      async () => new Response(null, { status: 500 }),
    );
    await expect(maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expectProbeV3(cache);
  });

  it('③ 非 ws url → null 且不落缓存（url 形状不是 endpoint 探测结论）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => {
      throw new Error('must not fetch');
    });
    await expect(maybeBuildA2AProvider({ ...CONFIG, url: 'http://x/rpc' }, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(cache.get(A2A_CHOICE_SESSION_KEY, 'http://x/rpc')).toBeUndefined();
  });

  it('④ 场景缺项（not_streaming）→ probe-v3 + null', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => new Response(
      JSON.stringify(makeEngineCard({ streaming: false })), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }));
    await expect(maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expectProbeV3(cache);
  });

  it('④ 场景缺项（missing_replay_extension）→ probe-v3 + null', async () => {
    const card = makeEngineCard();
    card.capabilities.extensions =
      card.capabilities.extensions.filter((e: any) => !e.url.includes('replay'));
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(
      async () => new Response(JSON.stringify(card), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }));
    await expect(maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expectProbeV3(cache);
  });

  it('⑤ 已 locked → 探测照跑但被锁吸收 → null（锁高于探测）', async () => {
    const cache = new TransportChoiceCache();
    cache.markLocked(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl);
    const fetchImpl = jest.fn(async () => okCardResponse());
    await expect(maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1); // 流程不因锁短路探测
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)?.kind)
      .toBe('locked-v3'); // decide 尊重 locked，不翻案
  });

  it('⑥ 开 + card 全过 → A2AProvider 实例（sendBan 单例注入 + a2aUrls 派生）', async () => {
    const cache = new TransportChoiceCache();
    const provider = await maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: okCardResponse as unknown as typeof fetch,
    });
    expect(provider).toBeInstanceOf(A2AProvider);
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)?.kind).toBe('a2a');
    // 私有成员经测试只读断言：地址派生 + 禁令注册表与门/宿主同源
    expect((provider as any).config.a2aUrls.rpcUrl).toBe(URLS.rpcUrl);
    expect((provider as any).config.a2aUrls.cardUrl).toBe(URLS.cardUrl);
    expect((provider as any).sendBan).toBe(getSendBanRegistry());
  });

  it('⑦ HITL 蓄意不参与启用：缺 resolve 声明照样启用（审批面运行期兜）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => new Response(
      JSON.stringify(makeEngineCard({ resolve: false })), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }));
    const provider = await maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(provider).toBeInstanceOf(A2AProvider);
  });
});

// ─── 同步捷径 + 预热时序 ─────────────────────────────────────────────────

describe('takeCachedA2AProvider（同步工厂捷径）', () => {
  it('缓存未决 → null + 后台预热（在飞去重：重复取只一发探测）→ 落定后同步命中', async () => {
    const cache = new TransportChoiceCache();
    let settleFetch!: (r: Response) => void;
    const fetchImpl = jest.fn(
      () => new Promise<Response>((res) => { settleFetch = res; }),
    );
    const deps = (): A2aBootstrapDeps => ({
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    expect(takeCachedA2AProvider(CONFIG, deps())).toBeNull();
    expect(takeCachedA2AProvider(CONFIG, deps())).toBeNull(); // 在飞去重
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    settleFetch(okCardResponse());
    await new Promise((r) => { setTimeout(r, 0); });   // 预热收敛（fetch→decide 微任务链）
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)?.kind).toBe('a2a');

    // 同步命中：不再探测即得实例
    const hit = takeCachedA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: mustNotFetch as unknown as typeof fetch,
    });
    expect(hit).toBeInstanceOf(A2AProvider);
  });

  it('probe-v3 命中 → null + 重探测翻案（重探测唯一触发点）', async () => {
    const cache = new TransportChoiceCache();
    cache.set(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl, 'probe-v3');
    const fetchImpl = jest.fn(async () => okCardResponse());
    expect(takeCachedA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await new Promise((r) => { setTimeout(r, 0); });
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)?.kind).toBe('a2a');
  });

  it('locked-v3 命中 → null 且不发探测（页内最高优先级）', () => {
    const cache = new TransportChoiceCache();
    cache.markLocked(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl);
    const fetchImpl = jest.fn(async () => okCardResponse());
    expect(takeCachedA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('开关关（缺省）→ null + 零探测', () => {
    const fetchImpl = jest.fn(async () => okCardResponse());
    expect(takeCachedA2AProvider(CONFIG)).toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

// ─── 运行期锁定接线（§3.3：locked 唯一来源） ───────────────────────────────

describe('isLockCandidateError（锁候选分类）', () => {
  it('auth/protocol typed → 锁候选', () => {
    expect(isLockCandidateError(Object.assign(new Error('a'), {
      failure: { kind: 'auth' },
    }))).toBe(true);
    expect(isLockCandidateError(Object.assign(new Error('p'), {
      failure: { kind: 'protocol' },
    }))).toBe(true);
  });

  it('transport/rpc/无 typed（探测失败）/null → 非锁候选', () => {
    expect(isLockCandidateError(Object.assign(new Error('t'), {
      failure: { kind: 'transport' },
    }))).toBe(false);
    expect(isLockCandidateError(Object.assign(new Error('r'), {
      failure: { kind: 'rpc' },
    }))).toBe(false);
    expect(isLockCandidateError(new Error('a2a probe failed: timeout')))
      .toBe(false);
    expect(isLockCandidateError(new Error('plain'))).toBe(false);
    expect(isLockCandidateError(null)).toBe(false);
    expect(isLockCandidateError(undefined)).toBe(false);
  });
});

describe('运行期硬失败 → locked（端到端接线：auth 持续拒绝）', () => {
  it('首请求 wire 级 auth 拒绝 → status error 播报 → markLocked → 后续构造回 v3', async () => {
    const cache = new TransportChoiceCache();
    // card 探测过、RPC 面持续回 -32000 auth_required（引擎鉴权门口径：
    // 200 + JSON-RPC error 信封，非 HTTP 401/403）
    const fetchImpl = jest.fn(async (u: string) => {
      if (String(u).endsWith('agent-card.json')) return okCardResponse();
      return new Response(JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        error: {
          code: -32000,
          message: 'auth denied',
          data: { reason: 'auth_required' },
        },
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    const provider = await maybeBuildA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(provider).toBeInstanceOf(A2AProvider);

    // request：connect（card 探测复用同一 fetchImpl）→ streamRpc 收 auth 信封
    // → typed auth（pre-task 分支重分类 prior_to_task_event 前已播报 status error）
    await expect(provider!.request({ query: 'q', sessionKey: 's1' }))
      .rejects.toThrow();
    expect(cache.get(A2A_CHOICE_SESSION_KEY, URLS.rpcUrl)?.kind)
      .toBe('locked-v3');

    // 锁后：同步捷径不再回 A2A（也绝不重探测）
    const fetchCalls = fetchImpl.mock.calls.length;
    expect(takeCachedA2AProvider(CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: mustNotFetch as unknown as typeof fetch,
    })).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(fetchCalls);
  });
});

// ─── §3.1 send-ban 门（纯函数面） ─────────────────────────────────────────

describe('isSendBannedForSession + deriveEndpointBase（§3.1 发送门）', () => {
  it('endpointBase 派生：ws url → rpc url；非 ws → null', () => {
    expect(deriveEndpointBase(WS_URL)).toBe(URLS.rpcUrl);
    expect(deriveEndpointBase('http://x/rpc')).toBeNull();
    expect(deriveEndpointBase('')).toBeNull();
  });

  it('挂禁命中即拦、不同 context 不拦、confirmed 核销后放行', () => {
    const registry = createSendBanRegistry();
    const base = deriveEndpointBase(WS_URL)!;
    registry.begin(base, 'ctx-1');
    expect(isSendBannedForSession('ctx-1', WS_URL, undefined, registry)).toBe(true);
    expect(isSendBannedForSession('ctx-2', WS_URL, undefined, registry)).toBe(false);
    registry.releaseOnConfirmedAbort(base, 'ctx-1');
    expect(isSendBannedForSession('ctx-1', WS_URL, undefined, registry)).toBe(false);
  });

  it('非 ws url 恒 false（v3 发送不受影响）', () => {
    const registry = createSendBanRegistry();
    registry.begin('http://x/rpc', 'ctx-1');
    expect(isSendBannedForSession('ctx-1', 'http://x/rpc', undefined, registry)).toBe(false);
  });
});


// ─── B17 服务端签发面（a2aBaseUrl 优先权） ───────────────────────────────

const A2A_BASE_URL = 'http://arca-x-svc.stable.alipay.net:32196';
const A2A_URLS = a2aUrlsFromBaseUrl(A2A_BASE_URL);
const SIGNED_CONFIG: A2aBootstrapConfig = {
  ...CONFIG,
  // 网关 relay topo 的 ws url——按旧推导会派生出一个 404 的 A2A 面
  //（正是 B17 存在的原因）；此处钉「签发值豁免一切 url 形状判断」。
  url: 'wss://gw.example/openapi/v1/bots/messages/ws/abc/api/openclaw/ws?x-proxypass-token=tok',
  a2aBaseUrl: A2A_BASE_URL,
};

describe('B17 决策表补充行：a2aBaseUrl 在场（resolveA2aUrls 单源）', () => {
  it('resolveA2aUrls：签发值在场压过 ws 推导；缺席回退 ws 推导', () => {
    expect(resolveA2aUrls(SIGNED_CONFIG).rpcUrl).toBe(A2A_URLS.rpcUrl);
    expect(resolveA2aUrls(SIGNED_CONFIG).cardUrl).toBe(A2A_URLS.cardUrl);
    expect(resolveA2aUrls(CONFIG).rpcUrl).toBe(URLS.rpcUrl);
  });

  it('签发值在场 + card 全过 → 探测/缓存/构造全部锚定签发地址', async () => {
    const cache = new TransportChoiceCache();
    const provider = await maybeBuildA2AProvider(SIGNED_CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: okCardResponse as unknown as typeof fetch,
    });
    expect(provider).toBeInstanceOf(A2AProvider);
    expect(cache.get(A2A_CHOICE_SESSION_KEY, A2A_URLS.rpcUrl)?.kind).toBe('a2a');
    expect((provider as any).config.a2aUrls.rpcUrl).toBe(A2A_URLS.rpcUrl);
    expect((provider as any).config.a2aUrls.cardUrl).toBe(A2A_URLS.cardUrl);
  });

  it('签发值在场时 ws url 形状不再裁决入口（观测到的旧缺口：relay url→404 派生）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async (_u: unknown) => okCardResponse());
    const provider = await maybeBuildA2AProvider(SIGNED_CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(provider).toBeInstanceOf(A2AProvider);
    // 探测命中的是签发地址的 card，不是从 relay ws url 派生的任何东西
    expect(String(fetchImpl.mock.calls[0][0])).toBe(A2A_URLS.cardUrl);
  });

  it('签发值非法（ws scheme）→ 回 v3 拦截在 url 解析层，不落缓存', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn();
    await expect(maybeBuildA2AProvider({ ...SIGNED_CONFIG, a2aBaseUrl: 'ws://host:1' }, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(cache.get(A2A_CHOICE_SESSION_KEY, A2A_URLS.rpcUrl)).toBeUndefined();
  });

  it('探测失败落在签发地址的键上（probe-v3 可翻案，键口径与构造同源）', async () => {
    const cache = new TransportChoiceCache();
    const fetchImpl = jest.fn(async () => { throw new TypeError('network down'); });
    await expect(maybeBuildA2AProvider(SIGNED_CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })).resolves.toBeNull();
    expect(cache.get(A2A_CHOICE_SESSION_KEY, A2A_URLS.rpcUrl)?.kind).toBe('probe-v3');
  });

  it('takeCachedA2AProvider：缓存键随签发地址；命中同步换入', async () => {
    const cache = new TransportChoiceCache();
    let settleFetch!: (r: Response) => void;
    const fetchImpl = jest.fn(
      (_u: unknown) => new Promise<Response>((res) => { settleFetch = res; }),
    );
    const deps = (): A2aBootstrapDeps => ({
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(takeCachedA2AProvider(SIGNED_CONFIG, deps())).toBeNull();
    expect(String(fetchImpl.mock.calls[0][0])).toBe(A2A_URLS.cardUrl);
    settleFetch(okCardResponse());
    await new Promise((r) => { setTimeout(r, 0); });
    const hit = takeCachedA2AProvider(SIGNED_CONFIG, {
      choiceCache: cache,
      switchOn: () => true,
      fetchImpl: mustNotFetch as unknown as typeof fetch,
    });
    expect(hit).toBeInstanceOf(A2AProvider);
    expect((hit as any).config.a2aUrls.rpcUrl).toBe(A2A_URLS.rpcUrl);
  });
});

describe('B17 发送门：签发面在场时端点键同源切换', () => {
  it('签发值在场 → 禁令键按签发地址解析（与 provider 注册面同键）', () => {
    const registry = createSendBanRegistry();
    registry.begin(A2A_URLS.rpcUrl, 'ctx-1');
    // 旧口径的 ws 推导键（relay url 派生）≠ 签发键：若门不切换键，此断言
    // 必然失败——正是把 a2aBaseUrl 接进门解析的原因。
    expect(deriveEndpointBase(SIGNED_CONFIG.url)).not.toBe(A2A_URLS.rpcUrl);
    expect(isSendBannedForSession('ctx-1', SIGNED_CONFIG.url, A2A_BASE_URL, registry)).toBe(true);
    expect(isSendBannedForSession('ctx-2', SIGNED_CONFIG.url, A2A_BASE_URL, registry)).toBe(false);
  });

  it('签发值缺省 → 行为逐字节保持旧口径（回归线）', () => {
    const registry = createSendBanRegistry();
    const base = deriveEndpointBase(WS_URL)!;
    registry.begin(base, 'ctx-1');
    expect(isSendBannedForSession('ctx-1', WS_URL, undefined, registry)).toBe(true);
  });
});

describe('app 级单例（context 级生存期）', () => {
  it('getSendBanRegistry 稳定同一实例（跨 provider 换代共享）', () => {
    expect(getSendBanRegistry()).toBe(getSendBanRegistry());
    expect(typeof SEND_BAN_UI_TEXT).toBe('string');
    expect(SEND_BAN_UI_TEXT).toBe('该会话暂不可发送（底层执行停止未确认）');
  });
});
