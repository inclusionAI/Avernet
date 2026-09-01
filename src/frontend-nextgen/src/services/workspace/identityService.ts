import type { IdentityView } from '@/domain/collaboration';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { listBots } from '@/services/backendApi/bots/botController';
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

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function readString(value: unknown): string | undefined {
  if (isNonEmptyString(value)) return value.trim();
  const record = asRecord(value);
  if (!record) return undefined;
  return [record.name, record.type, record.value, record.engine].find(isNonEmptyString)?.trim();
}

/**
 * 引擎字段和展示名称不是同一个维度：
 * engine={ name: 'TeamClaw网关', type: 'claude_code' } 时，必须取 type。
 * 只有在没有真实引擎枚举时，才允许回退到 name（例如 provider.name）。
 */
function readEngineString(value: unknown): string | undefined {
  if (isNonEmptyString(value)) return value.trim();
  const record = asRecord(value);
  if (!record) return undefined;
  return [
    record.type,
    record.engine,
    record.value,
    record.active_engine,
    record.engine_type,
    record.engine_name,
    record.name,
  ]
    .find(isNonEmptyString)
    ?.trim();
}

function readNestedString(
  record: Record<string, unknown>,
  path: string[],
  reader: (value: unknown) => string | undefined = readString,
): string | undefined {
  let current: unknown = record;
  for (const key of path) {
    const currentRecord = asRecord(current);
    if (!currentRecord) return undefined;
    current = currentRecord[key];
  }
  return reader(current);
}

function readBotId(value: unknown): string | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  return [record.bot_id, record.id, record.botId, record.uuid].find(isNonEmptyString)?.trim();
}

function readBotEngine(value: unknown): string | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  return (
    [record.engine, record.active_engine, record.engine_type, record.engine_name]
      .map(readEngineString)
      .find(isNonEmptyString) ??
    readNestedString(record, ['engine_info'], readEngineString) ??
    readNestedString(record, ['runtime', 'engine'], readEngineString) ??
    readNestedString(record, ['provider', 'name'])
  );
}

function readBotType(value: unknown): string | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  return [record.bot_type, record.botType].find(isNonEmptyString)?.trim();
}

function readPageItems<T = unknown>(response: unknown): T[] {
  const root = asRecord(response);
  const data = root?.data;
  if (Array.isArray(data)) return data as T[];
  const dataRecord = asRecord(data);
  if (Array.isArray(dataRecord?.items)) return dataRecord.items as T[];
  if (Array.isArray(dataRecord?.bots)) return dataRecord.bots as T[];
  if (Array.isArray(root?.items)) return root.items as T[];
  if (Array.isArray(root?.bots)) return root.bots as T[];
  return [];
}

function readBotSupplement(value: unknown): (BotSupplement & { botId: string }) | null {
  const record = asRecord(value);
  const botId = readBotId(value);
  if (!record || !botId) return null;

  // /openapi/v1/bots 的不同版本曾使用 engine、active_engine、engine_type 或嵌套引擎对象；
  // 这里仅在传输层做兼容，不把版本差异泄漏给 IdentityView/UI。
  return { botId, engine: readBotEngine(record), botType: readBotType(record) };
}

function normalizeBotId(botId: string) {
  const separatorIndex = botId.indexOf(':');
  return separatorIndex > 0 ? botId.slice(0, separatorIndex) : botId;
}

interface BotSupplement {
  engine?: string;
  botType?: string;
}

async function enrichBotMetadata<T extends { id: string; kind: string; engine?: string; botType?: string }>(
  items: T[],
  userId?: string,
): Promise<T[]> {
  const missingMetadata = items.some(
    (item) => item.kind === 'bot' && (!isNonEmptyString(item.engine) || !isNonEmptyString(item.botType)),
  );
  if (!missingMetadata) return items;

  try {
    const response = await listBots({
      page: 1,
      page_size: 100,
      ...(userId ? { user_id: userId } : {}),
    });
    const metadataByBotId = new Map<string, BotSupplement>();
    readPageItems(response).forEach((summary) => {
      const parsed = readBotSupplement(summary);
      if (!parsed || (!parsed.engine && !parsed.botType)) return;
      const { botId, ...metadata } = parsed;
      metadataByBotId.set(botId, metadata);
      const normalizedId = normalizeBotId(botId);
      if (!metadataByBotId.has(normalizedId)) metadataByBotId.set(normalizedId, metadata);
    });

    return items.map((item) => {
      if (item.kind !== 'bot' || (isNonEmptyString(item.engine) && isNonEmptyString(item.botType))) {
        return item;
      }
      const metadata = metadataByBotId.get(item.id) ?? metadataByBotId.get(normalizeBotId(item.id));
      if (!metadata) return item;
      return {
        ...item,
        engine: isNonEmptyString(item.engine) ? item.engine : metadata.engine,
        botType: isNonEmptyString(item.botType) ? item.botType : metadata.botType,
      };
    });
  } catch {
    // engine 仅为身份辅助展示信息；补充接口失败时保留 mine 结果，不阻断身份加载。
    return items;
  }
}

/** 实际拉取（无去重）；由 loadIdentities 单飞包装。 */
async function doLoadIdentities(): Promise<DomainResult<LoadIdentitiesResult>> {
  try {
    const resp = await listMyBots({ offset: 0, limit: 50 });
    const mineItems = readPageItems(resp);
    const humanItem = mineItems.find((item) => asRecord(item)?.kind === 'human');
    const humanUserId = normalizeOpenApiUserId(readBotId(humanItem));
    const mapped: IdentityView[] = await enrichBotMetadata(
      mineItems.flatMap((value) => {
        const b = asRecord(value);
        const id = readBotId(value);
        if (!b || !id) return [];
        const status = readString(b.status);
        return [
          {
            id,
            kind: b.kind === 'human' ? 'user' : 'bot',
            displayName: readString(b.name) ?? '未命名',
            avatarUrl: readString(b.avatar_url),
            online: status === 'online',
            status: status === 'online' ? 'online' : 'hidden',
            reachability: b.reachability === 'unreachable' ? 'unreachable' : 'reachable',
            engine: readBotEngine(value),
            botType: readBotType(value),
          },
        ];
      }),
      humanUserId,
    );
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
