import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import { listCollaboratingBots, type CollaboratingBotDto } from '@/services/backendApi/bots/collaboratingBotController';
import { mapBotActionPermissions } from './botActionPermissionMapper';
import { resolveUserId, type ChatBotView } from './botSessionService';
import type { DomainResult } from './identityService';

const PAGE_SIZE = 100;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function readString(...values: unknown[]): string | undefined {
  const value = values.find((item) => typeof item === 'string' && item.trim());
  return typeof value === 'string' ? value.trim() : undefined;
}

/** 与 managed Bot 目录一致：模板信息可能分布在顶层、engine_properties、template_config 或 bot_template_config。 */
function readTemplateType(dto: CollaboratingBotDto): string | undefined {
  const engineProperties = asRecord(dto.engine_properties);
  const templateConfig = asRecord(engineProperties.template_config ?? dto.template_config);
  return readString(
    dto.template_type ?? undefined,
    engineProperties.template_type,
    templateConfig.template_type,
    templateConfig.template_key,
  );
}

/** 历史引擎按工坊契约归一模板副维度；例如 aicoding 缺省对应 personalCoding。 */
function normalizeTemplateType(engine: string, templateType: string | undefined) {
  if (templateType) return templateType;
  const normalizedEngine = engine
    .trim()
    .toLowerCase()
    .replace(/[\s_-]/g, '');
  if (normalizedEngine === 'aicoding') return 'personalCoding';
  if (normalizedEngine === 'applicationcoding') return 'applicationCoding';
  return undefined;
}

function readTemplateName(dto: CollaboratingBotDto, templateType: string | undefined): string | undefined {
  const engineProperties = asRecord(dto.engine_properties);
  const templateConfig = asRecord(engineProperties.template_config ?? dto.template_config);
  const botTemplateConfig = asRecord(templateConfig.bot_template_config ?? dto.bot_template_config);
  const name = readString(
    dto.template_name,
    engineProperties.template_name,
    templateConfig.template_name,
    botTemplateConfig.template_name,
  );
  if (name) return name;

  const normalizedTemplateType = templateType?.toLowerCase().replace(/[\s_-]/g, '');
  if (normalizedTemplateType === 'applicationcoding') return '应用 Bot';
  if (normalizedTemplateType === 'personalcoding') return '个人 Coding Bot';
  return undefined;
}

function toTeamBot(dto: CollaboratingBotDto): ChatBotView {
  const realBotId = dto.bot_id.trim();
  const ownerId = dto.entity_id?.trim() || undefined;
  const botId = ownerId ? `${realBotId}:${ownerId}` : realBotId;
  const declaredTemplateType = readTemplateType(dto);
  const templateType = normalizeTemplateType(dto.engine, declaredTemplateType);
  const templateName = readTemplateName(dto, templateType);
  const permissions = mapBotActionPermissions(undefined, undefined);
  const runtime = resolveBotRuntime({
    botId: realBotId,
    engine: dto.engine,
    botType: dto.bot_type,
    templateType,
    templateName,
  });
  return {
    botId,
    realBotId,
    ownerId,
    engineOwnerId: dto.owner_id?.trim() || undefined,
    isTeamBot: true,
    displayName: dto.bot_name?.trim() || realBotId,
    engine: dto.engine,
    botType: dto.bot_type,
    online: dto.status === 'ACTIVE' || dto.status === 'online',
    // 实例运行状态透传(2026-10-21):FAILED/PENDING 供行级禁用与悬停提示感知,团队成员不再对不可用 Bot 盲操作。
    status: dto.status,
    reachability: 'reachable',
    chatable: Boolean(realBotId && ownerId),
    ...permissions,
    runtimeStage: dto.bot_type === 'service' ? 'online' : 'draft',
    isAgentCodingBot: runtime.isAgentCodingBot,
    templateType: runtime.templateType,
    templateName: runtime.templateName,
    spaceId: dto.space?.space_id,
    spaceName: dto.space?.name,
  };
}

export const teamBotConversationService = {
  /** 取全目录后供本地搜索；不向该端点发送 keyword/owner 等不支持的参数。 */
  async listBots(userId: string, signal?: AbortSignal): Promise<DomainResult<ChatBotView[]>> {
    try {
      const requester = resolveUserId(userId);
      if (!requester) throw new Error('登录用户不可用');
      const bots = new Map<string, ChatBotView>();
      for (let page = 1; ; page += 1) {
        if (signal?.aborted) throw new Error('请求已取消');
        const response = await listCollaboratingBots(
          { user_id: requester, page, page_size: PAGE_SIZE, stage: 'online' },
          signal,
        );
        const data = response.data;
        if (
          response.code !== 200000 ||
          !data ||
          !Array.isArray(data.items) ||
          !Number.isFinite(data.total) ||
          data.total < 0
        ) {
          throw new Error(response.message || '团队 Bot 接口返回格式异常');
        }
        for (const dto of data.items) {
          if (typeof dto.bot_id !== 'string' || !dto.bot_id.trim()) continue;
          const bot = toTeamBot(dto);
          if (!bots.has(bot.botId)) bots.set(bot.botId, bot);
        }
        if (!data.items.length || page * PAGE_SIZE >= data.total) break;
      }
      if (signal?.aborted) throw new Error('请求已取消');
      return { ok: true, data: [...bots.values()] };
    } catch {
      return {
        ok: false,
        error: { code: 'TEAM_BOTS_LOAD_FAILED', friendlyMessage: '加载团队 Bot 失败，请稍后重试。', canRetry: true },
      };
    }
  },
};
