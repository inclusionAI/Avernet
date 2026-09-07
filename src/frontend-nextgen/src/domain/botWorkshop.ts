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
  publicationVersion?: number;
  liveVersion?: number;
  ownerId?: string;
  entityKey: string;
  name: string;
  description?: string;
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
  runtime: BotRuntimeDomain;
  harnessContext?: BotHarnessContext;
  healthScore?: number;
  healthyInstances?: number;
  totalInstances?: number;
  lock?: BotLock;
  completeness: BotCompleteness;
  warnings: string[];
  actions: string[];
  disabledActions: Record<string, string>;
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

export interface BotCreateAuthorization {
  type: 'authorization_required';
  botId: string;
  iframeUrl: string;
  redirectUrl: string;
  request: AvernetBotCreateRequest;
  agentCoding?: AgentCodingDraft;
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
  | 'restart_publish';

/** 卡片管理菜单可分发的动词（菜单路由键），是 BotInventoryAction 中管理类动词的子集。 */
export type BotManagementVerb = 'delete' | 'restart' | 'engine_restart' | 'upgrade' | 'restart_publish';

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
