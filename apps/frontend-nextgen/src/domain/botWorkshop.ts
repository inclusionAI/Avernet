import type { BotRuntime } from '@/adapters/bot-runtime/types';
import type { BotHarnessContext } from './botHealthCheck';

export type BotOwnership = 'personal' | 'team';
export type BotDeployment = 'local' | 'cloud';
export type BotServiceMode = 'non-service' | 'service';
export type BotLifecycle = 'draft' | 'deploying' | 'prestable' | 'running' | 'offline' | 'failed' | 'unknown';
export type BotRuntimeStage = 'draft' | 'verify' | 'online';
export type BotCompleteness = 'complete' | 'partial';

export interface BotLock {
  status: 'mine' | 'other';
  holderUserId?: string;
  holderName?: string;
  lockedAt?: string;
}

export interface BotRuntimeDomain extends BotRuntime {
  engine: string;
  capabilityProfile: {
    canPublish: boolean;
    canEdit: boolean;
    canChat: boolean;
    canViewLogs: boolean;
  };
  visibleInOpenCore: boolean;
}

export interface BotDomain {
  id: string;
  cardId?: string;
  avatarUrl?: string;
  publicationVersion?: number;
  liveVersion?: number;
  ownerId?: string;
  ownerName?: string;
  entityKey: string;
  name: string;
  description?: string;
  expertOriginName?: string;
  spaceId?: string;
  spaceName?: string;
  /** 授权能力只跟随接口返回的 Bot 所属空间类型，不从 Bot 类型或部署形态推断。 */
  spaceKind: BotOwnership;
  ownership: BotOwnership;
  deployment: BotDeployment;
  serviceMode: BotServiceMode;
  /** 列表卡片是否允许将非服务 Bot 升级为服务 Bot。 */
  canUpgradeToService: boolean;
  lifecycle: BotLifecycle;
  rawStatus?: string;
  rawPublishStatus?: string;
  rawDisplayState?: string;
  runtime: BotRuntimeDomain;
  harnessContext?: BotHarnessContext;
  healthScore?: number;
  healthyInstances?: number;
  totalInstances?: number;
  lock?: BotLock;
  /** 库存接口声明需要协作锁，未持锁时也保留获取入口。 */
  needsEditLock?: boolean;
  /** Bot 级协作关系；运行态不要求编辑锁，但仍允许管理已有协作锁。 */
  hasCollaborators?: boolean;
  completeness: BotCompleteness;
  warnings: string[];
  actions: string[];
  disabledActions: Record<string, string>;
  /**
   * 复制进度(derive-on-read);仅由复制流程创建的 dst bot 携带,后端按 apply+device
   * 状态实时推导。CONVERGED 后后端返回 null,此字段亦为 null —— 一个复制完成后的
   * bot 即普通 bot,不再展示复制进度/重试入口。未走复制流程的 bot 字段缺失(undefined)。
   */
  copyState?: BotCopyStateSummary | null;
}

/**
 * 复制状态机(与后端 `BotCopyStatus` 9 值枚举一一对应,锁定线上契约)。
 * INITIAL/CREATED/AUTH_PENDING/CONFIG_COPIED/FILE_SYNCED/BOT_STARTED/CAPSET_SYNCED
 * 为进行中态;CONVERGED 终态(成功);FAILED 终态(可重试)。
 */
export type BotCopyStatus =
  | 'INITIAL'
  | 'CREATED'
  | 'AUTH_PENDING'
  | 'CONFIG_COPIED'
  | 'FILE_SYNCED'
  | 'BOT_STARTED'
  | 'CAPSET_SYNCED'
  | 'CONVERGED'
  | 'FAILED';

/** 后端 `BotCopyStateSummary` 的 FE 镜像 —— 与 `ac_bot_copy` 记录关联的操作 id + 当前推导状态。 */
export interface BotCopyStateSummary {
  operationId: string;
  status: BotCopyStatus;
  sourceBotId: string;
}

export interface BotListQuery {
  currentUserId?: string;
  spaceId?: string;
  keyword?: string;
  engine?: string;
  deployment?: BotDeployment;
  serviceMode?: BotServiceMode;
  page?: number;
  pageSize?: number;
}

export interface BotListResult {
  items: BotDomain[];
  total?: number;
  page: number;
  pageSize: number;
  hasMore?: boolean;
  warnings: string[];
}

export type BotCreateScenario = 'local' | 'cloud';

export interface AgentCodingTemplateDraft {
  key: string;
  versionId: string;
  name: string;
  description?: string;
  engine: string;
  templateType: string;
  source: 'official' | 'market';
  fields: Array<Record<string, unknown>>;
  config: Record<string, unknown>;
}

export interface AgentCodingDraft {
  kind: 'applicationCoding' | 'template';
  template?: AgentCodingTemplateDraft;
  values: Record<string, unknown>;
}

export interface BotCreateInput {
  scenario: BotCreateScenario;
  name: string;
  description: string;
  engine: string;
  spaceId: string;
  ownership: BotOwnership;
  serviceMode: BotServiceMode;
  initialize: boolean;
  /** 创建成功后通过头像 OpenAPI 持久化；创建接口本身不接收该字段。 */
  avatarUrl?: string;
  local?: { machineId: string; mountPath: string };
  agentCoding?: AgentCodingDraft;
}

export interface BotCreateSpace {
  id: string;
  name: string;
  ownership: BotOwnership;
  canCreate: boolean;
}

export interface AvernetBotCreateRequest {
  bot_name: string;
  bot_desc: string;
  engine: string;
  cluster_name: 'ACRA' | 'ANDC';
  bot_type: 'personal' | 'service';
  space_id?: string;
  engine_properties?: {
    template_type?: string;
    template_config?: {
      template_key?: string;
      template_version_id?: string;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
}

export interface LocalBotAuthorizationRequest {
  bot_name: string;
  bot_desc: string;
  engine: string;
  machine_id: string;
  mount_path: string;
}

export interface BotCreateAuthorization {
  type: 'authorization_required';
  botId: string;
  iframeUrl: string;
  redirectUrl: string;
  request: AvernetBotCreateRequest | LocalBotAuthorizationRequest;
  agentCoding?: AgentCodingDraft;
  /** 授权完成后再持久化，避免尚未创建完成时写头像。 */
  avatarUrl?: string;
}

export type BotCreateResult =
  | { type: 'created'; bot: BotDomain }
  | {
      type: 'created_with_pending_after_create';
      bot: BotDomain;
      afterCreateFailures: Array<{ key: string; retryable: boolean; message: string }>;
    }
  | BotCreateAuthorization;

export interface BotCreateAuthorizationPollResult {
  status: string;
  message?: string;
  bot?: BotDomain;
  afterCreateFailures?: Array<{ key: string; retryable: boolean; message: string }>;
}

export interface BotCopyInput {
  sourceBotId: string;
  sourceOwnerId?: string;
  /** 仅名称与描述可编辑;其余字段均为源 Bot 透传只读值,锁定不可改。 */
  name: string;
  description: string;
  /** 透传只读:从源 Bot 继承,服务端校验服务化/引擎合法性,FE 不二次校验。 */
  engine?: string;
  spaceId?: string;
  ownership?: BotOwnership;
  serviceMode?: BotServiceMode;
  avatarUrl?: string;
  /** 排除的资源路径集合;含目录则整子树排除。空集 = 全量复制。 */
  excludes: string[];
}

/**
 * 复制结果(后端 `BotCopyResponse` 镜像):
 * - `issued` —— type=success:复制已提交,dst bot 由 create_job 异步创建,FE 刷新列表
 *   即可见并跟随 copy_state 进度。响应只回 bot_id,**无 BotDto**。
 * - `authorization_required` —— type=auth_required:内网 AgentPass 授权闸 pending,
 *   回 iframe/redirect。**与创建同款处理**:携带 `request`(AvernetBotCreateRequest)
 *   交由 `useBotCreateAuthorization` 轮询 dst bot 的 auth-status,ISSUED 后收敛 ——
 *   dst bot 在 ac_bots 已注册(即便仍在复制中),auth-status 端点对 copy dst 同样适用。
 */
export type BotCopyResult =
  | { type: 'issued'; botId: string; operationId: string }
  | BotCreateAuthorization;

/** 复制授权视图 —— 与 `BotCreateAuthorization` 同构,复用 `AuthorizationPanel` + 轮询。 */
export type BotCopyAuthorization = BotCreateAuthorization;

/**
 * 复制入口可用性。默认仅 openclaw 引擎、且当前用户为 Owner 或协作者含 edit 权限时显示;
 * PRD §状态前置:草稿/运行中/预发/下线/待预发发布可复制,部署中/失败/未知阻断(可见但禁用并给原因)。
 */
export function canCopyBot(
  bot: BotDomain,
  currentUserId?: string,
): {
  visible: boolean;
  enabled: boolean;
  disabledReason?: string;
} {
  const engineAllowsCopy = bot.runtime?.engine === 'openclaw';
  const canEdit = Boolean(
    currentUserId && (bot.ownerId === currentUserId || bot.actions.includes('edit')),
  );
  if (!engineAllowsCopy || !canEdit) return { visible: false, enabled: false };
  const blockedReasons: Partial<Record<BotLifecycle, string>> = {
    deploying: 'Bot 部署中,无法复制',
    failed: 'Bot 创建失败,无法复制',
    unknown: 'Bot 状态未知,无法复制',
  };
  const reason = blockedReasons[bot.lifecycle];
  return reason ? { visible: true, enabled: false, disabledReason: reason } : { visible: true, enabled: true };
}

export type BotAction =
  | 'view'
  | 'edit'
  | 'chat'
  | 'publish'
  | 'offline'
  | 'restart'
  | 'logs'
  | 'instances'
  | 'evaluation'
  | 'activate'
  | 'claim-lock'
  | 'authorize'
  | 'health-check';

export type BotInventoryAction =
  | 'view'
  | 'chat'
  | 'edit'
  | 'delete'
  | 'restart'
  | 'engine_restart'
  | 'upgrade'
  | 'restart_publish'
  | 'open_folder';

/** 卡片管理菜单可分发的动词（菜单路由键），是 BotInventoryAction 中管理类动词的子集。 */
export type BotManagementVerb = 'delete' | 'restart' | 'engine_restart' | 'upgrade' | 'restart_publish' | 'open_folder' | 'activate';

/** 重启发布的发布阶段：仅 prestable/online 存在已发布 runtime（Avernet #1911 词表口径）。 */
export type BotPublishRestartStage = 'prestable' | 'online';

/** 重启发布阶段 → 用户可读环境名。 */
export const restartPublishStageLabel: Record<BotPublishRestartStage, string> = {
  prestable: '预发',
  online: '线上',
};

/**
 * 由卡片生命周期推导重启发布阶段；draft/deploying/offline/failed/unknown 无发布 runtime，返回 undefined。
 * 该推导是 stage 的单一事实源：Service 请求与确认弹窗文案均消费此处，避免多处映射漂移。
 */
export function restartPublishStageOf(lifecycle: BotLifecycle): BotPublishRestartStage | undefined {
  return lifecycle === 'prestable' ? 'prestable' : lifecycle === 'running' ? 'online' : undefined;
}

export interface BotActionAvailability {
  action: BotAction;
  visible: boolean;
  enabled: boolean;
  disabledReason?: string;
  dangerous?: boolean;
}

export type { BotHarnessContext } from './botHealthCheck';
