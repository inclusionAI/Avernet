import type { BotRuntime } from '@/adapters/bot-runtime/types';
import type { BotHarnessContext } from './botHealthCheck';

export type BotOwnership = 'personal' | 'team';
export type BotDeployment = 'local' | 'cloud';
export type BotServiceMode = 'non-service' | 'service';
export type BotLifecycle = 'draft' | 'deploying' | 'prestable' | 'running' | 'offline' | 'unknown';
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
  ownerId?: string;
  entityKey: string;
  name: string;
  description?: string;
  spaceId?: string;
  ownership: BotOwnership;
  deployment: BotDeployment;
  serviceMode: BotServiceMode;
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

export interface BotCreateInput {
  scenario: BotCreateScenario;
  name: string;
  description: string;
  engine: string;
  spaceId: string;
  ownership: BotOwnership;
  serviceMode: BotServiceMode;
  initialize: boolean;
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
}

export interface BotCreateAuthorization {
  type: 'authorization_required';
  botId: string;
  iframeUrl: string;
  redirectUrl: string;
  request: AvernetBotCreateRequest;
}

export type BotCreateResult = { type: 'created'; bot: BotDomain } | BotCreateAuthorization;

export interface BotCreateAuthorizationPollResult {
  status: string;
  message?: string;
  bot?: BotDomain;
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

export interface BotActionAvailability {
  action: BotAction;
  visible: boolean;
  enabled: boolean;
  disabledReason?: string;
  dangerous?: boolean;
}

export type { BotHarnessContext } from './botHealthCheck';
