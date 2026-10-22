import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';
import { getBcnCapability, getServiceBotCapability } from './agentCodingTemplateService';
import { resolveBotLifecycle } from './botLifecycleResolver';
import type { BotDomain, BotRuntimeDomain } from './types';

const PUBLIC_ENGINES = new Set(['openclaw', 'hermes', 'teclaw']);
const INTERNAL_ENGINES = new Set(['claude_code', 'aicoding', 'claudeCode', 'applicationCoding']);
const VISIBLE_ENGINES = new Set([...PUBLIC_ENGINES, ...INTERNAL_ENGINES]);
const asString = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : undefined);
const asNumber = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);
const asRecord = (value: unknown): BackendUnknownRecord =>
  value && typeof value === 'object' ? (value as BackendUnknownRecord) : {};

function templateConfigFrom(dto: BackendUnknownRecord) {
  const engineProperties = asRecord(dto.engine_properties);
  const engineTemplateConfig = asRecord(engineProperties.template_config);
  const directTemplateConfig = asRecord(dto.template_config);
  return {
    engineProperties,
    engineTemplateConfig,
    directTemplateConfig,
    engineBotTemplateConfig: asRecord(engineTemplateConfig.bot_template_config),
    directBotTemplateConfig: asRecord(directTemplateConfig.bot_template_config),
    botTemplateConfig: asRecord(dto.bot_template_config),
  };
}

function templateTypeFrom(dto: BackendUnknownRecord) {
  const config = templateConfigFrom(dto);
  return (
    asString(dto.template_type) ??
    asString(config.engineProperties.template_type) ??
    asString(config.engineTemplateConfig.template_type) ??
    asString(config.directTemplateConfig.template_type)
  );
}

function templateNameFrom(dto: BackendUnknownRecord, templateType?: string) {
  const config = templateConfigFrom(dto);
  const name = [
    dto.template_name,
    config.engineProperties.template_name,
    config.engineTemplateConfig.template_name,
    config.engineBotTemplateConfig.template_name,
    config.directTemplateConfig.template_name,
    config.directBotTemplateConfig.template_name,
    config.botTemplateConfig.template_name,
  ]
    .map(asString)
    .find(Boolean);
  if (name) return name;
  const normalizedTemplateType = templateType?.toLowerCase().replace(/[\s_-]/g, '');
  if (normalizedTemplateType === 'applicationcoding') return '应用 Bot';
  if (normalizedTemplateType === 'personalcoding') return '个人 Coding Bot';
  return undefined;
}

function runtimeFrom(dto: BackendUnknownRecord, warnings: string[]): BotRuntimeDomain {
  const rawEngine = asString(dto.active_engine) ?? asString(dto.engine_type) ?? asString(dto.engine);
  const templateType = templateTypeFrom(dto);
  const bcnEnabled = getBcnCapability({ config: dto, raw: dto });
  const config = templateConfigFrom(dto);
  const ext = asRecord(dto.ext);
  const engineForm =
    asString(dto.engine_form) ??
    asString(config.engineProperties.engine_form) ??
    asString(config.engineTemplateConfig.engine_form) ??
    asString(config.directTemplateConfig.engine_form) ??
    asString(config.botTemplateConfig.engine_form) ??
    asString(config.directBotTemplateConfig.engine_form) ??
    asString(config.engineBotTemplateConfig.engine_form) ??
    asString(ext.engine_form);
  const runtime = resolveBotRuntime({
    engine: rawEngine,
    templateType,
    templateName: templateNameFrom(dto, templateType),
    engineForm,
    botType: asString(dto.bot_type),
    botId: asString(dto.bot_id),
  });
  let engine = runtime.engine;
  const known = Boolean(engine && (PUBLIC_ENGINES.has(engine) || INTERNAL_ENGINES.has(engine) || engine === 'moltis'));
  if (!known) {
    if (rawEngine) warnings.push(`未知引擎：${rawEngine}`);
    engine = 'unknown';
  }
  return {
    ...runtime,
    bcnEnabled: runtime.isAgentCodingBot ? bcnEnabled === true : undefined,
    engine,
    capabilityProfile: {
      canPublish: PUBLIC_ENGINES.has(engine),
      canEdit: known && engine !== 'moltis' && engine !== 'unknown',
      canChat: known,
      canViewLogs: true,
    },
    visibleInOpenCore: VISIBLE_ENGINES.has(engine),
  };
}

function lifecycle(dto: BackendUnknownRecord, warnings: string[]) {
  const isService =
    asString(dto.kind)?.toLowerCase() === 'service' || asString(dto.bot_type)?.toLowerCase() === 'service';
  return resolveBotLifecycle(dto, isService, warnings);
}

export function mapBotDto(dto: BackendUnknownRecord, addressedBotId?: string, currentUserId?: string) {
  const warnings: string[] = [];
  // 详情接口的内部运行时 bot_id 可能为 "default"；网关子资源必须继续使用
  // 页面地址中的 canonical Bot ID，避免编辑页后续请求串到 /bots/default/**。
  const id = asString(addressedBotId) ?? asString(dto.bot_id) ?? '';
  const space = dto.space && typeof dto.space === 'object' ? (dto.space as BackendUnknownRecord) : undefined;
  const ownerId = asString(dto.owner_entity_id) ?? asString(dto.owner_id);
  const spaceId = asString(dto.space_id) ?? asString(space?.space_id) ?? asString(dto.owner_entity_id);
  const spaceName = asString(dto.space_name) ?? asString(space?.space_name) ?? asString(space?.name);
  const avatarUrl = asString(dto.avatar_url) ?? asString(asRecord(dto.ext).avatar_url);
  const botType = asString(dto.bot_type) ?? 'personal';
  const inventoryKind = asString(dto.kind);
  const entityType = asString(dto.entity_type);
  const spaceKind = asString(space?.kind)?.toLowerCase();
  const ownership =
    entityType === 'proj' || entityType === 'team' || spaceKind === 'team' ? ('team' as const) : ('personal' as const);
  const entityId =
    asString(dto.entity_id) ??
    (ownership === 'team' ? undefined : asString(dto.owner_entity_id) ?? spaceId);
  const runtime = runtimeFrom(dto, warnings);
  const templateType = templateTypeFrom(dto);
  const deployment = botType === 'desktop' ? ('local' as const) : ('cloud' as const);
  // kind/bot_type 契约未约束大小写；与 lifecycle() 的防御口径保持一致，避免 'SERVICE' 被误判为 non-service。
  const serviceMode =
    inventoryKind?.toLowerCase() === 'service' || botType?.toLowerCase() === 'service'
      ? ('service' as const)
      : ('non-service' as const);
  const codingTemplate = runtime.isAgentCodingBot
    ? ({
        templateType,
        config: dto,
        raw: dto,
      } as never)
    : undefined;
  const serviceBotCapability = getServiceBotCapability(codingTemplate);
  const normalizedTemplateType = templateType?.toLowerCase().replace(/[\s_-]/g, '');
  const isHistoricalPersonalCodingBot = normalizedTemplateType === 'personalcoding';
  const canUpgradeToService = runtime.isAgentCodingBot
    ? serviceBotCapability === true || (serviceBotCapability === undefined && isHistoricalPersonalCodingBot)
    : ['openclaw', 'teclaw'].includes(runtime.engine);
  const lockRaw =
    dto.edit_lock && typeof dto.edit_lock === 'object'
      ? (dto.edit_lock as BackendUnknownRecord)
      : dto.lock && typeof dto.lock === 'object'
      ? (dto.lock as BackendUnknownRecord)
      : undefined;
  const item: BotDomain = {
    id,
    cardId: asString(dto.card_id),
    avatarUrl,
    publicationVersion: asNumber(dto.publication_version) ?? asNumber(asRecord(dto.publish).version),
    liveVersion: asNumber(dto.live_version),
    ownerId,
    ownerName: asString(dto.owner_name),
    entityKey: asString(dto.card_id) ?? `${spaceId ?? 'unknown'}:${botType}:${id}`,
    name: asString(dto.bot_name) ?? asString(dto.name) ?? '未命名 Bot',
    description: asString(dto.bot_desc) ?? asString(dto.description),
    spaceId,
    spaceName,
    spaceKind: spaceKind === 'team' ? 'team' : 'personal',
    ownership,
    deployment,
    serviceMode,
    canUpgradeToService,
    lifecycle: lifecycle(dto, warnings),
    rawStatus: asString(dto.status),
    rawPublishStatus: asString(asRecord(dto.publish).status) ?? asString(dto.publish_status),
    runtime,
    harnessContext: entityId
      ? {
          entityType: entityType ?? (ownership === 'team' ? 'team' : 'staff'),
          entityId,
          botPublishId:
            asString(dto.bot_publish_id) ?? asString(dto.publish_id) ?? asString(asRecord(dto.publish).id),
        }
      : undefined,
    healthScore: asNumber(dto.health_score),
    healthyInstances: asNumber(dto.healthy_instances),
    totalInstances: asNumber(dto.total_instances),
    needsEditLock: typeof lockRaw?.need_lock === 'boolean' ? lockRaw.need_lock : undefined,
    lock:
      lockRaw && (lockRaw.locked === true || !('locked' in lockRaw))
        ? {
            status:
              (Boolean(currentUserId) && asString(lockRaw.holder_user_id) === currentUserId) ||
              asString(lockRaw.status) === 'mine'
                ? 'mine'
                : 'other',
            holderUserId: asString(lockRaw.holder_user_id),
            holderName: asString(lockRaw.holder_name),
            lockedAt: asString(lockRaw.locked_at) ?? asString(lockRaw.acquired_at) ?? asString(lockRaw.created_at),
          }
        : undefined,
    completeness: warnings.length ? 'partial' : 'complete',
    warnings,
    actions: Array.isArray(dto.actions)
      ? dto.actions.filter((value): value is string => typeof value === 'string')
      : [],
    disabledActions:
      dto.disabled_actions && typeof dto.disabled_actions === 'object'
        ? Object.fromEntries(
            Object.entries(dto.disabled_actions as BackendUnknownRecord).filter(
              (entry): entry is [string, string] => typeof entry[1] === 'string',
            ),
          )
        : {},
  };
  return { item, warnings };
}

export function mapBotList(page?: BackendApiPage<BackendUnknownRecord>, currentUserId?: string) {
  const results = (page?.items ?? []).map((dto) => mapBotDto(dto, undefined, currentUserId));
  return {
    items: results.map((result) => result.item),
    total: page?.total,
    page: page?.page ?? 1,
    pageSize: page?.pageSize ?? page?.page_size ?? 20,
    hasMore: page?.hasMore,
    warnings: results.flatMap((result) => result.warnings),
  };
}
