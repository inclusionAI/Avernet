import type { LocalBotAuthorizationRequest } from '@/domain/botWorkshop';
import { restartPublishStageOf } from '@/domain/botWorkshop';
import {
  createBot,
  deleteBot,
  deleteLocalBot,
  deleteServiceDraft,
  getBot,
  listBotInventory,
  pollBotAuthStatus,
  restartBot,
  restartBotEngine,
  restartLocalBot,
  updateBot,
  upgradeBotToService,
} from '@/services/backendApi/bots/botController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import type { BackendUnknownRecord } from '@/services/backendApi/types';
import { runAfterCreateActions } from './agentCodingAfterCreateService';
import { agentCodingTemplateService, type AgentCodingTemplate } from './agentCodingTemplateService';
import { botAvatarService } from './botAvatarService';
import { personalCreateSpace, toBotCreateRequest, validateBotCreate } from './botCreatePolicy';
import { botEditorService } from './botEditorService';
import { mapBotDto, mapBotList } from './botMapper';
import { localBotService } from './localBotService';
import type {
  AvernetBotCreateRequest,
  BotCreateAuthorizationPollResult,
  BotCreateInput,
  BotCreateResult,
  BotCreateSpace,
  BotDomain,
  BotListQuery,
  BotListResult,
} from './types';

export interface BotWorkshopServiceOverview {
  module: string;
  description: string;
}

type AfterCreateFailure = { key: string; retryable: boolean; message: string };

async function persistCreatedAvatar(
  bot: BotDomain,
  avatarUrl?: string,
): Promise<{ bot: BotDomain; failure?: AfterCreateFailure }> {
  if (!avatarUrl) return { bot };
  try {
    const savedUrl = await botAvatarService.save(bot.id, avatarUrl);
    return { bot: { ...bot, avatarUrl: savedUrl } };
  } catch (error) {
    return {
      bot,
      failure: {
        key: 'avatar',
        retryable: true,
        message: error instanceof Error ? error.message : '头像保存失败',
      },
    };
  }
}

export const botWorkshopService = {
  getOverview(): BotWorkshopServiceOverview {
    return { module: 'botWorkshop', description: 'Bot 管理通过领域 Service 统一承载列表查询、映射和能力隔离。' };
  },
  async list(query: BotListQuery = {}): Promise<BotListResult> {
    const response = await listBotInventory(
      {
        keyword: query.keyword || undefined,
        engine: query.engine || undefined,
        deploy_mode: query.deployment,
        is_service: query.serviceMode === undefined ? undefined : query.serviceMode === 'service',
        page: query.page ?? 1,
        page_size: query.pageSize ?? 20,
      },
      query.spaceId || undefined,
    );
    const result = mapBotList(response.data, query.currentUserId);
    const visible = result.items.filter((item) => item.runtime.visibleInOpenCore);
    const pageNumber = query.page ?? result.page;
    const pageSize = query.pageSize ?? result.pageSize;
    return {
      ...result,
      items: visible,
      total: result.total,
      page: pageNumber,
      pageSize,
      hasMore: result.hasMore,
    };
  },
  async detail(id: string, ownerId?: string): Promise<BotDomain | undefined> {
    const response = await getBot(id, ownerId);
    const dto = response.data;
    if (!dto) return undefined;
    const result = mapBotDto(dto as BackendUnknownRecord, id);
    return result.item.runtime.visibleInOpenCore ? result.item : undefined;
  },
  getCreateSpaces(
    scenario: BotCreateInput['scenario'],
    currentSpaceId?: string,
    localUserId?: string,
    currentSpace?: BotCreateSpace,
  ): BotCreateSpace[] {
    const personal = personalCreateSpace(localUserId);
    if (scenario === 'local') return [personal];
    if (currentSpace) return [currentSpace];
    const spaces = personal.canCreate ? [personal] : [];
    if (currentSpaceId && currentSpaceId !== personal.id) {
      spaces.push({ id: currentSpaceId, name: '当前空间', ownership: 'team', canCreate: true });
    }
    return spaces;
  },
  validateCreate: validateBotCreate,
  toCreateRequest: toBotCreateRequest,
  async listAgentCodingTemplates() {
    return agentCodingTemplateService.list();
  },
  async create(input: BotCreateInput): Promise<BotCreateResult> {
    validateBotCreate(input);
    const normalized: BotCreateInput =
      input.scenario === 'local'
        ? { ...input, spaceId: personalCreateSpace().id, ownership: 'personal', serviceMode: 'non-service' }
        : input;
    const request = normalized.scenario === 'cloud' ? toBotCreateRequest(normalized) : undefined;
    if (normalized.scenario === 'local') {
      const localResult = await localBotService.create(normalized);
      if (localResult.type === 'authorization_required') {
        return normalized.avatarUrl ? { ...localResult, avatarUrl: normalized.avatarUrl } : localResult;
      }
      const avatar = await persistCreatedAvatar(localResult.bot, normalized.avatarUrl);
      const failures = [
        ...(localResult.type === 'created_with_pending_after_create' ? localResult.afterCreateFailures : []),
        ...(avatar.failure ? [avatar.failure] : []),
      ];
      return failures.length
        ? { type: 'created_with_pending_after_create', bot: avatar.bot, afterCreateFailures: failures }
        : { type: 'created', bot: avatar.bot };
    }
    const response = await createBot(request as unknown as BackendUnknownRecord);
    const dto = response.data;
    if (!dto) throw new Error('创建接口未返回 Bot 数据');
    if ('iframe_url' in dto || 'redirect_url' in dto) {
      const botId = typeof dto.bot_id === 'string' ? dto.bot_id : '';
      const iframeUrl = typeof dto.iframe_url === 'string' ? dto.iframe_url : '';
      const redirectUrl = typeof dto.redirect_url === 'string' ? dto.redirect_url : '';
      if (!botId || (!iframeUrl && !redirectUrl)) throw new Error('授权信息不完整，请稍后重试创建');
      return {
        type: 'authorization_required',
        botId,
        iframeUrl,
        redirectUrl,
        request: request!,
        agentCoding: normalized.agentCoding,
        ...(normalized.avatarUrl ? { avatarUrl: normalized.avatarUrl } : {}),
      };
    }
    let bot = mapBotDto(dto).item;
    const afterCreateFailures: AfterCreateFailure[] = [];
    if (normalized.agentCoding?.template) {
      const failures = await runAfterCreateActions({
        botId: bot.id,
        ownerId: bot.ownerId,
        template: normalized.agentCoding.template as AgentCodingTemplate,
        values: normalized.agentCoding.values,
      });
      afterCreateFailures.push(
        ...failures.map((failure) => ({
          key: failure.action.key,
          retryable: failure.action.retryable,
          message: failure.error.message,
        })),
      );
    }
    const avatar = await persistCreatedAvatar(bot, normalized.avatarUrl);
    bot = avatar.bot;
    if (avatar.failure) afterCreateFailures.push(avatar.failure);
    if (afterCreateFailures.length > 0) return { type: 'created_with_pending_after_create', bot, afterCreateFailures };
    return { type: 'created', bot };
  },
  async pollCreateAuthorization(
    botId: string,
    request: AvernetBotCreateRequest | LocalBotAuthorizationRequest,
    agentCoding?: BotCreateInput['agentCoding'],
    avatarUrl?: string,
  ): Promise<BotCreateAuthorizationPollResult> {
    let response;
    try {
      if ('machine_id' in request) {
        const result = await localBotService.poll(botId, request);
        if (result.status !== 'ISSUED' || !result.bot) return result;
        const avatar = await persistCreatedAvatar(result.bot, avatarUrl);
        return {
          ...result,
          bot: avatar.bot,
          afterCreateFailures: avatar.failure ? [avatar.failure] : undefined,
        };
      }
      response = await pollBotAuthStatus(botId, request as unknown as BackendUnknownRecord);
    } catch (error) {
      if (error instanceof BackendRequestError && error.data && typeof error.data === 'object') {
        const data = 'data' in error.data ? error.data.data : undefined;
        if (data && typeof data === 'object' && 'status' in data && typeof data.status === 'string') {
          return {
            status: data.status,
            message: 'message' in data && typeof data.message === 'string' ? data.message : error.message,
          };
        }
      }
      throw error;
    }
    const dto = response.data;
    if (!dto?.status) throw new Error('授权状态接口未返回有效状态');
    let bot = dto.bot ? mapBotDto(dto.bot, botId).item : undefined;
    if (dto.status === 'ISSUED' && bot) {
      const failures: AfterCreateFailure[] = [];
      if (agentCoding?.template) {
        const actionFailures = await runAfterCreateActions({
          botId: bot.id,
          ownerId: bot.ownerId,
          template: agentCoding.template as AgentCodingTemplate,
          values: agentCoding.values,
        });
        failures.push(
          ...actionFailures.map((failure) => ({
            key: failure.action.key,
            retryable: failure.action.retryable,
            message: failure.error.message,
          })),
        );
      }
      const avatar = await persistCreatedAvatar(bot, avatarUrl);
      bot = avatar.bot;
      if (avatar.failure) failures.push(avatar.failure);
      return {
        status: dto.status,
        message: dto.message,
        bot,
        afterCreateFailures: failures.length ? failures : undefined,
      };
    }
    return { status: dto.status, message: dto.message, bot };
  },
  async update(id: string, values: { name?: string; description?: string }) {
    const response = await updateBot(id, { bot_name: values.name, bot_desc: values.description });
    if (!response.data) throw new Error('更新接口未返回 Bot 数据');
    return mapBotDto(response.data).item;
  },
  async remove(bot: BotDomain) {
    if (bot.deployment === 'local') await deleteLocalBot(bot.id);
    else if (bot.serviceMode === 'service') await deleteServiceDraft(bot.id);
    else await deleteBot(bot.id);
  },
  async restart(bot: BotDomain) {
    if (bot.deployment === 'local') await restartLocalBot(bot.id);
    else await restartBot(bot.id);
  },
  async restartEngine(id: string) {
    await restartBotEngine(id);
  },
  /**
   * 重启发布的服务 runtime（`POST /bots/{id}/lifecycle/restart`，复用 botEditorService 既有封装）。
   * 动作名即路由键：`restart_publish` 只在服务预发/上线卡由后端授权，stage 推导收敛在
   * `restartPublishStageOf`（domain 单一事实源，确认弹窗文案同源消费）。
   */
  async restartPublish(bot: BotDomain) {
    const stage = restartPublishStageOf(bot.lifecycle);
    if (!stage) throw new Error('当前发布状态不支持重启发布');
    await botEditorService.restartLifecycle(bot.id, stage);
  },
  async enableService(id: string) {
    await upgradeBotToService(id);
  },
};
