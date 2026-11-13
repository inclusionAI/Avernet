import { hasServiceDraftEvidence } from '@/domain/botEngineVersion';
import type { BotLifecycle } from '@/domain/botWorkshop';
import type { BackendUnknownRecord } from '@/services/backendApi/types';

const DISPLAY_STATE_LIFECYCLE: Record<string, BotLifecycle> = {
  running: 'running', pending: 'deploying', failed: 'failed', dormant: 'offline',
  local_running: 'running', local_offline: 'offline', local_pending: 'deploying', local_failed: 'failed',
  service_draft: 'draft', service_deploying: 'deploying', service_prestable: 'prestable',
  service_staging: 'prestable', service_online: 'running', service_offline: 'offline',
};

const DEPLOYING_BOT_STATUSES = new Set(['PENDING', 'PROVISIONING', 'REACTIVATING']);
const FAILED_BOT_STATUSES = new Set(['FAILED', 'ERROR']);
const OFFLINE_BOT_STATUSES = new Set(['OFFLINE', 'RELEASED', 'RELEASING', 'RECYCLED']);
const DEPLOYING_PUBLISH_STATUSES = new Set(['BUILDING', 'BUILT', 'VALIDATE_PUB', 'ONLINE_PUB']);
const PRESTABLE_PUBLISH_STATUSES = new Set(['VALIDATING']);
const RUNNING_PUBLISH_STATUSES = new Set(['SUCCESS']);
const OFFLINE_PUBLISH_STATUSES = new Set(['RELEASED', 'UPGRADED']);

function asString(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function publishStatusOf(dto: BackendUnknownRecord) {
  const publish = dto.publish;
  if (publish && typeof publish === 'object') {
    const status = asString((publish as BackendUnknownRecord).status);
    if (status) return status.toUpperCase();
  }
  return asString(dto.publish_status)?.toUpperCase();
}

function legacyLifecycle(dto: BackendUnknownRecord, warnings: string[]): BotLifecycle {
  const rawStatus = asString(dto.status)?.toUpperCase();
  const personalCloud = asString(dto.bot_type)?.toLowerCase() !== 'desktop' &&
    asString(dto.kind)?.toLowerCase() !== 'local' && asString(dto.deploy_mode)?.toLowerCase() !== 'local';
  // 回收/激活是实体状态；旧 display_state 可能尚未更新，不能覆盖过渡态。
  if (personalCloud && (rawStatus === 'REACTIVATING' || rawStatus === 'RECYCLING')) return 'deploying';
  if (personalCloud && rawStatus === 'RECYCLED') return 'offline';
  const displayState = asString(dto.display_state)?.toLowerCase();
  if (displayState && DISPLAY_STATE_LIFECYCLE[displayState]) return DISPLAY_STATE_LIFECYCLE[displayState];
  const status = asString(dto.status)?.toUpperCase();
  const publishStatus = publishStatusOf(dto);
  if (!status && !publishStatus) { warnings.push('缺少生命周期状态'); return 'unknown'; }
  if (status === 'PENDING') return 'deploying';
  if (status === 'FAILED') return 'failed';
  if (status && OFFLINE_BOT_STATUSES.has(status)) return 'offline';
  if (status === 'ACTIVE' && !publishStatus) return 'running';
  if (status === 'ACTIVE' && PRESTABLE_PUBLISH_STATUSES.has(publishStatus ?? '')) return 'prestable';
  if (status === 'ACTIVE' && RUNNING_PUBLISH_STATUSES.has(publishStatus ?? '')) return 'running';
  if (publishStatus === 'DRAFT') return 'draft';
  return 'unknown';
}

/** 非草稿发布记录拥有独立运行态，源 Bot 的实例状态只用于草稿或无发布阶段的记录。 */
function serviceLifecycle(dto: BackendUnknownRecord, warnings: string[]): BotLifecycle {
  const status = asString(dto.status)?.toUpperCase();
  const publishStatus = publishStatusOf(dto);
  if (publishStatus === 'FAILED') return 'failed';
  if (publishStatus && DEPLOYING_PUBLISH_STATUSES.has(publishStatus)) return 'deploying';
  if (publishStatus && PRESTABLE_PUBLISH_STATUSES.has(publishStatus)) return 'prestable';
  if (publishStatus && RUNNING_PUBLISH_STATUSES.has(publishStatus)) return 'running';
  if (publishStatus && OFFLINE_PUBLISH_STATUSES.has(publishStatus)) return 'offline';
  // 升级会重启源 Bot 的草稿实例，不能把它的 PENDING/FAILED 等状态传播到线上版本。
  if (status && DEPLOYING_BOT_STATUSES.has(status)) return 'deploying';
  if (status && FAILED_BOT_STATUSES.has(status)) return 'failed';
  if (status && OFFLINE_BOT_STATUSES.has(status)) return 'offline';
  if (publishStatus === 'DRAFT') return 'draft';
  const displayState = asString(dto.display_state)?.toLowerCase();
  if (displayState && DISPLAY_STATE_LIFECYCLE[displayState]) return DISPLAY_STATE_LIFECYCLE[displayState];
  if (status === 'ACTIVE') return 'draft';
  if (!status) warnings.push('缺少服务 Bot 生命周期状态');
  return 'unknown';
}

export function resolveBotLifecycle(dto: BackendUnknownRecord, isService: boolean, warnings: string[]): BotLifecycle {
  return isService ? serviceLifecycle(dto, warnings) : legacyLifecycle(dto, warnings);
}


/** Version checks require explicit draft evidence; legacy ACTIVE is insufficient. */
export function resolveServiceEngineLifecycle(dto: BackendUnknownRecord): BotLifecycle {
  const state = resolveBotLifecycle(dto, true, []);
  return state === 'draft' && !hasServiceDraftEvidence(state, publishStatusOf(dto), asString(dto.display_state))
    ? 'unknown'
    : state;
}
