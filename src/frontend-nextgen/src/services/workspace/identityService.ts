import type { IdentityView } from '@/domain/collaboration';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { ENABLE_TEST_USER, TEST_USER_IDENTITY } from './testUser';

export interface DomainError {
  code: string;
  friendlyMessage: string;
  canRetry: boolean;
}
export type DomainResult<T> = { ok: true; data: T } | { ok: false; error: DomainError };

export interface LoadIdentitiesResult {
  identities: IdentityView[];
  defaultActiveId: string;
}

const PERSIST_KEY = 'teamclaw:workspace:lastIdentityId';

// 模块级单飞：并发调用复用同一次 bots/mine 请求，resolve 后释放。
// workspace 初始化与 admin/铃铛的 ensureUserId 补拉共用此处，避免跨模块重复请求同一身份。
let inflight: Promise<DomainResult<LoadIdentitiesResult>> | null = null;
// 曾成功加载标记：成功后置 true，供 useHumanIdentity 推导 ready/error 态。
// 注意：仅标记「曾经成功」，不保证 store 当前非空（store 可被外部清空再重载）。
let resolved = false;

/** 实际拉取（无去重）；由 loadIdentities 单飞包装。 */
async function doLoadIdentities(): Promise<DomainResult<LoadIdentitiesResult>> {
  try {
    const resp = await listMyBots({ offset: 0, limit: 50 });
    const mapped: IdentityView[] = (resp.data?.items ?? []).map((b) => ({
      id: b.bot_id,
      kind: b.kind === 'human' ? 'user' : 'bot',
      displayName: b.name ?? '未命名',
      avatarUrl: b.avatar_url,
      online: b.status === 'online',
      status: b.status === 'online' ? 'online' : 'hidden',
      reachability: b.reachability === 'unreachable' ? 'unreachable' : 'reachable',
    }));
    const humans = mapped.filter((i) => i.kind === 'user');
    const bots = mapped.filter((i) => i.kind === 'bot');
    // 真实「我」取自 mine 接口返回的 human 项（真实姓名/头像/在线状态）；
    // 仅当 mine 未返回 human 时回退到合成的「我」。
    const me: IdentityView = humans[0] ?? { id: 'me', kind: 'user', displayName: '我', online: true };
    const identities: IdentityView[] = [me, ...bots];
    // 尾部注入「测试用户」合成身份(示例,不从 mine 请求;ENABLE_TEST_USER=false 可一行下线)
    const withTestUser: IdentityView[] = ENABLE_TEST_USER ? [...identities, TEST_USER_IDENTITY] : identities;
    const persisted = typeof window !== 'undefined' ? window.localStorage.getItem(PERSIST_KEY) : null;
    const initial = withTestUser.find((i) => i.id === persisted)?.id ?? me.id;
    resolved = true;
    return { ok: true, data: { identities: withTestUser, defaultActiveId: initial } };
  } catch {
    return {
      ok: false,
      error: {
        code: 'IDENTITY_LOAD_FAILED',
        friendlyMessage: '加载可协作身份失败，请稍后重试。',
        canRetry: true,
      },
    };
  }
}

export const identityService = {
  /** 拉取可协作身份。模块级单飞：并发复用同一次 bots/mine，resolve 后释放。 */
  loadIdentities(): Promise<DomainResult<LoadIdentitiesResult>> {
    if (!inflight) {
      inflight = doLoadIdentities().finally(() => {
        inflight = null;
      });
    }
    return inflight;
  },
  persistLastIdentityId(id: string) {
    if (typeof window !== 'undefined') window.localStorage.setItem(PERSIST_KEY, id);
  },
  /** 是否正在加载（inflight 未释放）。供 useHumanIdentity 推导 loading 态。 */
  isIdentityLoading(): boolean {
    return !!inflight;
  },
  /** 是否曾成功加载过（即使 store 被清空仍 true，用于区分 error vs pending）。 */
  isIdentityResolved(): boolean {
    return resolved;
  },
};
