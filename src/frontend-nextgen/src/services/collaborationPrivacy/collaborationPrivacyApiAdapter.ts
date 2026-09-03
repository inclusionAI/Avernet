import {
  getCollaborationBot,
  listBots,
  listMyBots,
  patchCollaborationBot,
  type CollaborationBotDto,
  type CollaborationBotPatchBody,
  type ListMyBotsParams,
  type OwnedBotDto,
} from '@/services/backendApi';
import { isEnvelopeFailure } from '@/services/backendApi/types';

/**
 * 已冻结 Gateway 的协作权限 API 接缝。
 * 它覆盖当前 mine/detail/PATCH 能力；好友策略字段来自已部署 Avernet owner-scoped PATCH DTO，
 * 由 Runtime Adapter 负责 friend_ext 的读取、合并与结果校验。
 */
export interface CollaborationPrivacyApiAdapter {
  listManagedBots(params?: ManagedBotListParams, signal?: AbortSignal): Promise<ManagedBotPage>;
  getManagedBot(botId: string, signal?: AbortSignal): Promise<CollaborationBotDto>;
  patchManagedBot(botId: string, body: CollaborationBotPatchBody, signal?: AbortSignal): Promise<CollaborationBotDto>;
}

export interface ManagedBotListParams extends ListMyBotsParams {
  /** 仅供 /openapi/v1/bots 目录接口使用，不透传到 /collaboration/bots/mine。 */
  user_id?: string;
}

export interface ManagedBotPage {
  items: CollaborationBotDto[];
  total: number;
  offset: number;
  limit: number;
}

interface CollaborationPrivacyApiDependencies {
  listMyBots: typeof listMyBots;
  getCollaborationBot: typeof getCollaborationBot;
  patchCollaborationBot: typeof patchCollaborationBot;
  listBots?: typeof listBots;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isVisibility(value: unknown): value is NonNullable<CollaborationBotDto['visibility']> {
  return value === 'public' || value === 'protected' || value === 'private';
}

function isFriendCheckInStrategy(
  value: unknown,
): value is NonNullable<CollaborationBotDto['friend_check_in_strategy']> {
  return value === 'OPEN' || value === 'APPROVAL' || value === 'DEPT_FREE';
}

function isDescriptor(value: unknown): value is CollaborationBotDto['descriptor'] {
  return (
    isRecord(value) &&
    typeof value.summary === 'string' &&
    isStringArray(value.domains) &&
    isStringArray(value.scopes) &&
    Array.isArray(value.skills) &&
    value.skills.every(
      (skill) =>
        isRecord(skill) &&
        isNonEmptyString(skill.name) &&
        (skill.description === undefined || typeof skill.description === 'string'),
    )
  );
}

function hasCommonBotFields(value: Record<string, unknown>) {
  return (
    isNonEmptyString(value.bot_id) &&
    isNonEmptyString(value.name) &&
    isVisibility(value.visibility) &&
    (value.user_visibility === undefined || isVisibility(value.user_visibility)) &&
    (value.status === 'online' || value.status === 'hidden') &&
    isNonEmptyString(value.env) &&
    isNonNegativeInteger(value.created_at) &&
    isNonNegativeInteger(value.updated_at) &&
    (value.created_by === undefined || isNonEmptyString(value.created_by))
  );
}

function isCollaborationBotDto(value: unknown): value is CollaborationBotDto {
  if (!isRecord(value) || !hasCommonBotFields(value)) {
    return false;
  }

  if (value.kind === 'human') {
    return (
      value.descriptor === undefined &&
      value.reachability === undefined &&
      value.provider === undefined &&
      value.agent_code === undefined
    );
  }

  if (value.kind !== 'bot') {
    return false;
  }

  const providerValid =
    value.provider === undefined ||
    (isRecord(value.provider) && isNonEmptyString(value.provider.provider_id) && isNonEmptyString(value.provider.name));

  return (
    isDescriptor(value.descriptor) &&
    (value.reachability === 'reachable' || value.reachability === 'unreachable') &&
    (value.agent_code === undefined || typeof value.agent_code === 'string') &&
    (value.friend_ext === undefined || (isRecord(value.friend_ext) && !Array.isArray(value.friend_ext))) &&
    (value.friend_check_in_strategy === undefined || isFriendCheckInStrategy(value.friend_check_in_strategy)) &&
    providerValid
  );
}

function assertBusinessSuccess(response: { code?: string | number }, label: string) {
  // mine 历史接口使用 20000；通用 /bots 接口遵循 200000 信封。两种已确认成功码均兼容。
  if (isEnvelopeFailure(response) && Number(response.code) !== 20000) {
    throw new Error(`${label}接口返回了无法识别的业务码`);
  }
}

function isOwnedBotSummary(value: unknown): value is OwnedBotDto {
  return (
    isRecord(value) &&
    isNonEmptyString(value.bot_id) &&
    isNonEmptyString(value.bot_name) &&
    (value.engine === undefined || isNonEmptyString(value.engine))
  );
}

function normalizeBotId(botId: string) {
  const normalized = botId.trim();
  const separatorIndex = normalized.indexOf(':');
  return separatorIndex > 0 ? normalized.slice(0, separatorIndex) : normalized;
}

function enrichManagedBotsWithEngine(items: CollaborationBotDto[], summaries: OwnedBotDto[]) {
  const exactEngineByBotId = new Map(
    summaries
      .filter((summary) => isNonEmptyString(summary.engine))
      .map((summary) => [summary.bot_id, summary.engine!.trim()]),
  );
  const normalizedEngineByBotId = new Map<string, string>();
  summaries.forEach((summary) => {
    if (!isNonEmptyString(summary.engine)) return;
    const normalizedId = normalizeBotId(summary.bot_id);
    if (!normalizedEngineByBotId.has(normalizedId)) {
      normalizedEngineByBotId.set(normalizedId, summary.engine.trim());
    }
  });

  return items.map((item) => {
    const engine = exactEngineByBotId.get(item.bot_id) ?? normalizedEngineByBotId.get(normalizeBotId(item.bot_id));
    return engine ? { ...item, engine } : item;
  });
}

async function listEngineSummaries(
  dependency: typeof listBots,
  signal?: AbortSignal,
  userId?: string,
): Promise<OwnedBotDto[]> {
  const response = await dependency({ ...(userId ? { user_id: userId } : {}), page: 1, page_size: 100 }, signal);
  assertBusinessSuccess(response, 'Bot 引擎列表');
  const items = response.data?.items;
  if (!Array.isArray(items) || !items.every(isOwnedBotSummary)) {
    throw new Error('Bot 引擎列表接口返回了无法识别的数据');
  }
  return items;
}

export function createCollaborationPrivacyApiAdapter(
  dependencies: CollaborationPrivacyApiDependencies,
): CollaborationPrivacyApiAdapter {
  return {
    async listManagedBots(params = {}, signal) {
      const { user_id: engineUserId, ...mineParams } = params;
      const response = await dependencies.listMyBots(mineParams, signal);
      assertBusinessSuccess(response, 'Bot 列表');
      const page = response.data;
      if (
        !page ||
        !Array.isArray(page.items) ||
        !page.items.every(isCollaborationBotDto) ||
        !isNonNegativeInteger(page.total) ||
        !isNonNegativeInteger(page.offset) ||
        !isNonNegativeInteger(page.limit)
      ) {
        throw new Error('Bot 列表接口返回了无法识别的数据');
      }
      let items = page.items;
      if (dependencies.listBots) {
        try {
          items = enrichManagedBotsWithEngine(
            page.items,
            await listEngineSummaries(dependencies.listBots, signal, engineUserId),
          );
        } catch (error) {
          // 引擎标签是补充信息；/bots 失败不能阻断 mine 和后续 BCSFuse 配置查询。
          if (signal?.aborted || (error as Error).name === 'AbortError') throw error;
        }
      }
      return {
        items,
        total: page.total,
        offset: page.offset,
        limit: page.limit,
      };
    },

    async getManagedBot(botId, signal) {
      const response = await dependencies.getCollaborationBot(botId, signal);
      assertBusinessSuccess(response, 'Bot 详情');
      if (!isCollaborationBotDto(response.data)) {
        throw new Error('Bot 详情接口返回了无法识别的数据');
      }
      if (response.data.bot_id !== botId) {
        throw new Error('Bot 详情接口返回了不匹配的 Bot');
      }
      return response.data;
    },

    async patchManagedBot(botId, body, signal) {
      const response = await dependencies.patchCollaborationBot(botId, body, signal);
      assertBusinessSuccess(response, 'Bot 更新');
      if (!isCollaborationBotDto(response.data)) {
        throw new Error('Bot 更新接口返回了无法识别的数据');
      }
      if (response.data.bot_id !== botId) {
        throw new Error('Bot 更新接口返回了不匹配的 Bot');
      }
      return response.data;
    },
  };
}

export const collaborationPrivacyApiAdapter = createCollaborationPrivacyApiAdapter({
  listMyBots,
  getCollaborationBot,
  patchCollaborationBot,
  listBots,
});
