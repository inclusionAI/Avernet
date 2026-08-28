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
import { mapBotDto, mapBotList } from './botMapper';
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

const PUBLIC_ENGINES = new Set(['openclaw', 'claude_code', 'aicoding', 'hermes', 'teclaw']);
const SERVICE_ENGINES = new Set(['openclaw', 'claude_code', 'teclaw']);
function configuredLocalUserId() {
  return typeof TEAMCLAW_OPENAPI_USER_ID === 'string' ? TEAMCLAW_OPENAPI_USER_ID.trim() : '';
}

function personalSpace(userId = configuredLocalUserId()): BotCreateSpace {
  const localUserId = userId.trim();
  return {
    id: localUserId ? `personal:${localUserId}` : '',
    name: '个人空间',
    ownership: 'personal',
    canCreate: Boolean(localUserId),
  };
}

function validateCreate(input: BotCreateInput) {
  const name = input.name.trim();
  if (!name) throw new Error('请输入 Bot 名称');
  if (name.includes('@')) throw new Error('Bot 名称不能包含 @');
  if (name.length > 40) throw new Error('Bot 名称不能超过 40 个字符');
  if (!PUBLIC_ENGINES.has(input.engine)) throw new Error('请选择可用的公开引擎');
  if (input.scenario === 'cloud' && input.serviceMode === 'service' && !SERVICE_ENGINES.has(input.engine)) {
    const engineName = input.engine === 'hermes' ? 'Hermes' : input.engine === 'aicoding' ? 'AIcoding' : '当前引擎';
    throw new Error(`${engineName} 暂不支持服务化`);
  }
  if (input.scenario === 'cloud' && !input.spaceId.trim()) throw new Error('请选择有效的归属空间');
}

function toCreateRequest(input: BotCreateInput): AvernetBotCreateRequest {
  validateCreate(input);
  return {
    bot_name: input.name.trim(),
    bot_desc: input.description.trim(),
    engine: input.engine,
    cluster_name: input.engine === 'teclaw' ? 'ANDC' : 'ACRA',
    bot_type: input.serviceMode === 'service' ? 'service' : 'personal',
    space_id: input.spaceId || undefined,
  };
}

export const botWorkshopService = {
  getOverview(): BotWorkshopServiceOverview {
    return { module: 'botWorkshop', description: 'Bot 工坊通过领域 Service 统一承载列表查询、映射和能力隔离。' };
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
    const result = mapBotList(response.data);
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
  async detail(id: string): Promise<BotDomain | undefined> {
    const response = await getBot(id);
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
    const personal = personalSpace(localUserId);
    if (scenario === 'local') return [personal];
    if (currentSpace?.ownership === 'personal') return [currentSpace];
    const spaces = personal.canCreate ? [personal] : [];
    if (currentSpace && currentSpace.id !== personal.id) {
      spaces.push(currentSpace);
    } else if (currentSpaceId && currentSpaceId !== personal.id) {
      spaces.push({ id: currentSpaceId, name: '当前空间', ownership: 'team', canCreate: true });
    }
    return spaces;
  },
  validateCreate,
  toCreateRequest,
  async create(input: BotCreateInput): Promise<BotCreateResult> {
    validateCreate(input);
    const normalized: BotCreateInput =
      input.scenario === 'local'
        ? { ...input, spaceId: personalSpace().id, ownership: 'personal', serviceMode: 'non-service' }
        : input;
    const request = normalized.scenario === 'cloud' ? toCreateRequest(normalized) : undefined;
    if (normalized.scenario === 'local')
      throw new Error('本地 Bot 创建需要先选择已绑定设备和工作目录，当前 OpenAPI 工作流尚未接入此表单');
    const response = await createBot(request as unknown as BackendUnknownRecord);
    const dto = response.data;
    if (!dto) throw new Error('创建接口未返回 Bot 数据');
    if ('iframe_url' in dto || 'redirect_url' in dto) {
      const botId = typeof dto.bot_id === 'string' ? dto.bot_id : '';
      const iframeUrl = typeof dto.iframe_url === 'string' ? dto.iframe_url : '';
      const redirectUrl = typeof dto.redirect_url === 'string' ? dto.redirect_url : '';
      if (!botId || (!iframeUrl && !redirectUrl)) throw new Error('授权信息不完整，请稍后重试创建');
      return { type: 'authorization_required', botId, iframeUrl, redirectUrl, request: request! };
    }
    return { type: 'created', bot: mapBotDto(dto).item };
  },
  async pollCreateAuthorization(
    botId: string,
    request: AvernetBotCreateRequest,
  ): Promise<BotCreateAuthorizationPollResult> {
    let response;
    try {
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
    return {
      status: dto.status,
      message: dto.message,
      bot: dto.bot ? mapBotDto(dto.bot, botId).item : undefined,
    };
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
  async enableService(id: string) {
    await upgradeBotToService(id);
  },
};
