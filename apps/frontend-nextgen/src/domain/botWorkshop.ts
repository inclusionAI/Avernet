import type { BotRuntime } from '@/adapters/bot-runtime/types';
import type { BotHarnessContext } from './botHealthCheck';

export type BotOwnership = 'personal' | 'team';
export type BotDeployment = 'local' | 'cloud';
export type BotServiceMode = 'non-service' | 'service';
export type BotLifecycle = 'draft' | 'deploying' | 'prestable' | 'running' | 'offline' | 'failed' | 'unknown';
export type BotRuntimeStage = 'draft' | 'verify' | 'online';
export const serviceBotStageLabel: Record<BotRuntimeStage, string> = {
  draft: '草稿',
  verify: '试运行',
  online: '正式运行',
};
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

export interface BotRestartTracking {
  /** Coding 重启 journal 的 operation_id；仅前端追踪使用，不来自库存接口。 */
  operationId: string;
  startedAt: number;
  deadlineAt: number;
  /** 库存已读到过渡态（PENDING 等）后置位；此后再读到 ACTIVE 视为重启完成。 */
  sawProgress?: boolean;
  /** 超时后经详情接口确认仍在执行时的延长次数；当前最多延长一次。 */
  extensions?: number;
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
  /** 服务 Bot 评测入口是否可见；随评测门禁白名单实时查询结果刷新，同时控制发起评测和历史评测记录入口。 */
  evalGateVisible?: boolean;
  /** 当前用户是否有该服务 Bot 的编辑权限；控制历史评测记录入口。 */
  canOpenEvalHistory?: boolean;
  /** 库存接口声明需要协作锁，未持锁时也保留获取入口。 */
  needsEditLock?: boolean;
  /** Bot 级协作关系；运行态不要求编辑锁，但仍允许管理已有协作锁。 */
  hasCollaborators?: boolean;
  /** 客户端重启追踪标记：coding 重启为异步 journal，在库存读到过渡态前保持「重启中」展示。 */
  pendingRestart?: BotRestartTracking;
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
 * 复制入口可用性。非 openclaw 引擎不展示(与后端 `_COPYABLE_ENGINES={"openclaw"}` 一致);
 * openclaw 引擎按 Bot 类型分流裁决,前置门(权限/锁)优先于 lifecycle 文案。
 *
 * - 个人 Bot(`serviceMode !== 'service'`):仅 owner 可复制;非 owner 展示置灰「仅Bot Owner可复制」。
 * - 服务 Bot(`serviceMode === 'service'`):
 *   - 无操作权限(`actions` 仅剩 view/chat)→ 置灰「无 Bot 操作权限,请先申请」(对齐
 *     `getBotCollaborationMode` 的协作身份口径,普通空间成员落此档)。
 *   - owner 且无协作者(`hasCollaborators` 非 true)→ 免锁直放行:无协作者时锁表短路,
 *     owner 永远读不到 lock,与后端「无协作者 owner 免锁」一致。
 *   - 其余情形必须本人持锁(`lock.status === 'mine'`)才放行;他人持锁 → 置灰「Bot 正被他人编辑,请稍后再试」;
 *     无人持锁 → 置灰「请先获取锁」。
 * - 通过前置门后,lifecycle 处于 deploying/failed/unknown → 置灰现有中文阻断原因;其余放行。
 */
const COPY_LIFECYCLE_BLOCKED: Partial<Record<BotLifecycle, string>> = {
  deploying: 'Bot 部署中,无法复制',
  failed: 'Bot 创建失败,无法复制',
  unknown: 'Bot 状态未知,无法复制',
};

/** 操作权限信号:`actions` 除 view/chat 外是否还有动作(owner/已批准协作者落此)。与 `getBotCollaborationMode` 同口径。 */
const NON_STANDING_ACTIONS = new Set(['view', 'chat']);

function copyAvailabilityFromLifecycle(
  bot: BotDomain,
): { visible: boolean; enabled: boolean; disabledReason?: string } {
  const reason = COPY_LIFECYCLE_BLOCKED[bot.lifecycle];
  return reason
    ? { visible: true, enabled: false, disabledReason: reason }
    : { visible: true, enabled: true };
}

export function canCopyBot(
  bot: BotDomain,
  currentUserId?: string,
): {
  visible: boolean;
  enabled: boolean;
  disabledReason?: string;
} {
  if (bot.runtime?.engine !== 'openclaw') return { visible: false, enabled: false };
  const isOwner = Boolean(currentUserId && bot.ownerId === currentUserId);

  // 个人 Bot(非服务):仅 owner 可复制。
  if (bot.serviceMode !== 'service') {
    if (!isOwner) {
      return { visible: true, enabled: false, disabledReason: '仅Bot Owner可复制' };
    }
    return copyAvailabilityFromLifecycle(bot);
  }

  // 服务 Bot:先验操作权限,再验锁,最后 lifecycle。
  const hasOperationPermission = bot.actions.some((action) => !NON_STANDING_ACTIONS.has(action));
  if (!hasOperationPermission) {
    return { visible: true, enabled: false, disabledReason: '无 Bot 操作权限,请先申请' };
  }
  // owner + 无协作者:锁表短路读不到 lock,免锁直放行(与后端 owner 免锁一致)。
  if (isOwner && !bot.hasCollaborators) {
    return copyAvailabilityFromLifecycle(bot);
  }
  // 多人协作下复制要求本人持锁(只读判断,不在此抢锁)。
  const lock = bot.lock;
  if (lock?.status === 'mine') return copyAvailabilityFromLifecycle(bot);
  if (lock?.status === 'other') {
    return { visible: true, enabled: false, disabledReason: 'Bot 正被他人编辑,请稍后再试' };
  }
  return { visible: true, enabled: false, disabledReason: '请先获取锁' };
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
  prestable: serviceBotStageLabel.verify,
  online: serviceBotStageLabel.online,
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
