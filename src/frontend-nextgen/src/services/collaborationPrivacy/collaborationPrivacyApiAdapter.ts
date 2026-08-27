import {
  getCollaborationBot,
  listMyBots,
  patchCollaborationBot,
  type CollaborationBotDto,
  type CollaborationBotPatchBody,
  type ListMyBotsParams,
} from '@/services/backendApi';

/**
 * 已冻结 Gateway 的协作权限 API 接缝。
 * 它只覆盖当前 Swagger 已确认的 mine/detail/PATCH 能力；组织、公开范围审批和好友策略
 * 仍由页面级 Gateway 的 Mock/Unsupported Adapter 承担，避免用默认值伪造真实状态。
 */
export interface CollaborationPrivacyApiAdapter {
  listManagedBots(params?: ListMyBotsParams, signal?: AbortSignal): Promise<ManagedBotPage>;
  getManagedBot(botId: string, signal?: AbortSignal): Promise<CollaborationBotDto>;
  patchManagedBot(botId: string, body: CollaborationBotPatchBody, signal?: AbortSignal): Promise<CollaborationBotDto>;
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
    (value.visibility === 'public' || value.visibility === 'protected' || value.visibility === 'private') &&
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
    providerValid
  );
}

function assertBusinessSuccess(response: { code?: string | number }, label: string) {
  if (Number(response.code) !== 20000) {
    throw new Error(`${label}接口返回了无法识别的业务码`);
  }
}

export function createCollaborationPrivacyApiAdapter(
  dependencies: CollaborationPrivacyApiDependencies,
): CollaborationPrivacyApiAdapter {
  return {
    async listManagedBots(params = {}, signal) {
      const response = await dependencies.listMyBots(params, signal);
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
      return {
        items: page.items,
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
});
