export type BotEngineConfig = Record<string, unknown>;

export interface BotEditorSkill {
  id: string;
  name: string;
  description?: string;
  version?: string;
  source?: 'local' | 'teamclaw-market' | 'skillcenter-market' | 'workshop';
  homepageUrl?: string;
  active: boolean;
}

export interface BotEditorMcp {
  serverCode: string;
  name: string;
  description?: string;
  active: boolean;
  source?: 'market' | 'workshop';
}

export interface BotEditorCli {
  code: string;
  name: string;
  description?: string;
}

export interface BotCapabilitySet {
  id: string;
  name: string;
  description?: string;
  isDefault: boolean;
  active: boolean;
  skills: BotEditorSkill[];
  mcps: BotEditorMcp[];
  clis: BotEditorCli[];
}

export interface BotRenderScreen {
  id: number;
  name: string;
  cdnUrl: string;
  creatorId: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface BotRenderScreenInput {
  name: string;
  cdnUrl: string;
}

export type ServicePublicationStatus = 'draft' | 'deploying' | 'prestable' | 'staging' | 'running' | 'offline';
export type ServicePublicationAction =
  | 'publish_staging'
  | 'publish_online'
  | 'restart_publish'
  | 'cancel_staging'
  | 'upgrade'
  | 'offline'
  | 'retry'
  | 'delete';

export interface ServicePublication {
  publicationId: number;
  cardId: string;
  version: number;
  status: ServicePublicationStatus;
  internalStatus: string;
  liveVersion?: number;
  availableActions: ServicePublicationAction[];
  deployment?: { action: string; target: string; status: 'running' | 'failed'; errorMessage?: string };
  approval?: { required: boolean; status?: string; approvalId?: string; approvalUrl?: string };
  createdAt: string;
  updatedAt: string;
}

export interface BotEditorResource {
  path: string;
  /** 请求该条目时使用的目录；空字符串表示根目录响应。 */
  parentPath: string;
  name: string;
  type: 'file' | 'folder';
  size?: number;
}

export interface BotEditorRoutine {
  id: string;
  name: string;
  cron: string;
  command: string;
  enabled: boolean;
  timezone?: string;
  modifiedAt?: string;
}

export interface BotEditorRoutineInput {
  name: string;
  cron: string;
  command: string;
  enabled: boolean;
  timezone?: string;
}
export interface BotEditorRoutineRun {
  id: string;
  status: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface BotEditorEngineStatus {
  engine: string;
  activeConnections: number;
  running: boolean;
}
