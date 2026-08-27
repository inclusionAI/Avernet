import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import type { BackendApiPage, BackendUnknownRecord } from '@/services/backendApi/types';
import type { BotDomain, BotLifecycle, BotRuntimeDomain } from './types';

const PUBLIC_ENGINES = new Set(['openclaw', 'hermes', 'teclaw']);
const INTERNAL_ENGINES = new Set(['claude_code', 'aicoding', 'claudeCode', 'applicationCoding']);
const VISIBLE_ENGINES = new Set([...PUBLIC_ENGINES, ...INTERNAL_ENGINES]);
const DISPLAY_STATE_LIFECYCLE: Record<string, BotLifecycle> = {
  running: 'running',
  pending: 'deploying',
  failed: 'unknown',
  dormant: 'offline',
  local_running: 'running',
  local_offline: 'offline',
  local_pending: 'deploying',
  local_failed: 'unknown',
  service_draft: 'draft',
  service_deploying: 'deploying',
  service_prestable: 'prestable',
  service_staging: 'prestable',
  service_online: 'running',
  service_offline: 'offline',
};
const asString = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : undefined);
const asNumber = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : undefined);

function runtimeFrom(dto: BackendUnknownRecord, warnings: string[]): BotRuntimeDomain {
  const rawEngine = asString(dto.active_engine) ?? asString(dto.engine_type) ?? asString(dto.engine);
  const templateType = asString(dto.template_type);
  let engine = rawEngine ?? 'unknown';
  if (rawEngine === 'aicoding') engine = 'claudeCode';
  const known = Boolean(engine && (PUBLIC_ENGINES.has(engine) || INTERNAL_ENGINES.has(engine) || engine === 'moltis'));
  if (!known) {
    if (rawEngine) warnings.push(`未知引擎：${rawEngine}`);
    engine = 'unknown';
  }
  const runtime = resolveBotRuntime({
    engine,
    templateType,
    botType: asString(dto.bot_type),
    botId: asString(dto.bot_id),
  });
  return {
    ...runtime,
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
  const displayState = asString(dto.display_state)?.toLowerCase();
  if (displayState && DISPLAY_STATE_LIFECYCLE[displayState]) {
    return DISPLAY_STATE_LIFECYCLE[displayState];
  }
  const status = asString(dto.status)?.toUpperCase();
  const publish = asString(dto.publish_status)?.toLowerCase();
  const isService =
    asString(dto.kind)?.toLowerCase() === 'service' || asString(dto.bot_type)?.toLowerCase() === 'service';
  if (!status && !publish) {
    warnings.push('缺少生命周期状态');
    return 'unknown' as const;
  }
  if (status === 'PENDING') return 'deploying' as const;
  if (status === 'FAILED') return 'unknown' as const;
  if (status === 'OFFLINE' || status === 'RELEASED' || status === 'RECYCLED') return 'offline' as const;
  // Bot 详情接口没有 inventory 的 display_state / publication 状态。服务 Bot 的草稿运行时
  // 同样会返回 ACTIVE，不能把它当成 online；只有明确发布状态或 inventory 展示态才能判定已上线。
  if (status === 'ACTIVE' && !publish) return isService ? ('draft' as const) : ('running' as const);
  if (status === 'ACTIVE' && ['validating', 'validate_pub'].includes(publish ?? '')) return 'prestable' as const;
  if (status === 'ACTIVE' && ['success', 'online_pub', 'upgraded', 'built'].includes(publish ?? ''))
    return 'running' as const;
  if (publish === 'draft') return 'draft' as const;
  return 'unknown' as const;
}

export function mapBotDto(dto: BackendUnknownRecord, addressedBotId?: string) {
  const warnings: string[] = [];
  // 详情接口的内部运行时 bot_id 可能为 "default"；网关子资源必须继续使用
  // 页面地址中的 canonical Bot ID，避免编辑页后续请求串到 /bots/default/**。
  const id = asString(addressedBotId) ?? asString(dto.bot_id) ?? '';
  const space = dto.space && typeof dto.space === 'object' ? (dto.space as BackendUnknownRecord) : undefined;
  const ownerId = asString(dto.owner_entity_id);
  const spaceId = asString(dto.space_id) ?? asString(space?.space_id) ?? asString(dto.owner_entity_id);
  const entityId = asString(dto.entity_id) ?? asString(dto.owner_entity_id) ?? spaceId;
  const botType = asString(dto.bot_type) ?? 'personal';
  const inventoryKind = asString(dto.kind);
  const entityType = asString(dto.entity_type);
  const spaceKind = asString(space?.kind);
  const ownership =
    entityType === 'proj' || entityType === 'team' || spaceKind === 'team' ? ('team' as const) : ('personal' as const);
  const runtime = runtimeFrom(dto, warnings);
  const deployment = botType === 'desktop' ? ('local' as const) : ('cloud' as const);
  const serviceMode =
    inventoryKind === 'service' || botType === 'service' ? ('service' as const) : ('non-service' as const);
  const item: BotDomain = {
    id,
    ownerId,
    entityKey: `${spaceId ?? 'unknown'}:${botType}:${id}`,
    name: asString(dto.bot_name) ?? asString(dto.name) ?? '未命名 Bot',
    description: asString(dto.bot_desc) ?? asString(dto.description),
    spaceId,
    ownership,
    deployment,
    serviceMode,
    lifecycle: lifecycle(dto, warnings),
    rawStatus: asString(dto.status),
    rawPublishStatus: asString(dto.publish_status),
    runtime,
    harnessContext: entityId
      ? {
          entityType: entityType ?? (ownership === 'team' ? 'team' : 'staff'),
          entityId,
          botPublishId: asString(dto.bot_publish_id) ?? asString(dto.publish_id),
        }
      : undefined,
    healthScore: asNumber(dto.health_score),
    healthyInstances: asNumber(dto.healthy_instances),
    totalInstances: asNumber(dto.total_instances),
    lock:
      dto.lock && typeof dto.lock === 'object'
        ? {
            status: asString((dto.lock as BackendUnknownRecord).status) === 'mine' ? 'mine' : 'other',
            holderUserId: asString((dto.lock as BackendUnknownRecord).holder_user_id),
            holderName: asString((dto.lock as BackendUnknownRecord).holder_name),
            lockedAt:
              asString((dto.lock as BackendUnknownRecord).locked_at) ??
              asString((dto.lock as BackendUnknownRecord).created_at),
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

export function mapBotList(page?: BackendApiPage<BackendUnknownRecord>) {
  const results = (page?.items ?? []).map((dto) => mapBotDto(dto));
  return {
    items: results.map((result) => result.item),
    total: page?.total,
    page: page?.page ?? 1,
    pageSize: page?.pageSize ?? page?.page_size ?? 20,
    hasMore: page?.hasMore,
    warnings: results.flatMap((result) => result.warnings),
  };
}
