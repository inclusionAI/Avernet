/** Read-only management models. Credentials are deliberately absent from this boundary. */
export interface ManagementAccess {
  canView: boolean;
  canEdit: boolean;
}
export interface ManagementVersionSource {
  kind: string;
  deployNumber: number;
  baselineDeployNumber: number | null;
  taskId: string | null;
  revision: number | null;
  automationRoundId: string | null;
  actorId: string | null;
  effectiveAtMs: number | null;
  restoredFromDeployNumber: number | null;
  restoredSource?: ManagementVersionSource | null;
}
export interface ManagementHistoryItem {
  version: number;
  deployNumber: number;
  tagName: string | null;
  action: string;
  fromDeployNumber: number | null;
  note: string | null;
  botId: string | null;
  ownerId: string | null;
  isActive: boolean;
  createdAtMs: number;
  source: ManagementVersionSource | null;
}
export interface ManagementHistory {
  history: ManagementHistoryItem[];
  active: ManagementHistoryItem | null;
}
export interface ManagementSnapshot {
  workflowId: string;
  version: number;
  deployNumber: number;
  tagName: string | null;
  action: string;
  note: string | null;
  createdAtMs: number;
  specJson: string;
}
export interface ManagementPermission {
  id: number;
  botId: string | null;
  botOwnerId: string;
  canView: boolean;
  canExecute: boolean;
  canEdit: boolean;
}
export interface ManagementFacade {
  workflowId: string;
  command: string;
  packId: string | null;
  remark: string | null;
}
export interface ManagementEventOptions {
  title?: string;
  includeRunLink?: boolean;
  includeNodeOutput?: boolean;
  includeFinalOutput?: boolean;
  maxOutputFields?: number;
  maxOutputValueLen?: number;
}
export interface ManagementNotification {
  workflowId: string;
  robotCode: string;
  hasSecret: boolean;
  onFailureUsers: { userId: string; name: string | null }[];
  onFailureGroups: { openConversationId: string; name: string | null }[];
  onFailureMessageTitle: string | null;
  onFailureMessageIncludeRunLink: boolean;
  notifyOn: string[] | null;
  eventOptions: Record<string, ManagementEventOptions>;
}
export interface ManagementCallback {
  configId: string;
  workflowId: string;
  name: string;
  url: string;
  hasSecret: boolean;
  enabled: boolean;
  notifyOn: string[];
  timeoutMs: number;
  maxRetries: number;
  retryDelayMs: number;
  includeNodeOutput: boolean;
  createdAtMs: number;
  modifiedAtMs: number;
}
export interface ManagementPolicy {
  workflowId: string;
  enabled: boolean;
  autoDraft: boolean;
  dailyTime: string;
  timezone: string;
  executionEnvironment: 'pre' | 'prod' | null;
  minimumImpact: 'medium' | 'high' | null;
  notificationRecipients: string[] | null;
  notificationVersion: number | null;
  version: number;
  updatedAtMs: number;
  dailyConfigured: boolean;
  source: 'database' | 'environment' | 'default';
}
export interface ManagementJob {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  reason: string | null;
  taskId: string | null;
  createdAtMs: number | null;
  flowId: string | null;
  analysisId: string | null;
  complete: boolean | null;
}
export interface ManagementAutomationStatus {
  workflowId: string;
  lastScannedAtMs: number | null;
  pauseReason: string | null;
  coverage: { total: number; pending: number; failed: number; partial: number; unknown: number };
  jobs: ManagementJob[];
  retryableJobs: ManagementJob[] | null;
  round: {
    id: string;
    total: number;
    batches: number;
    completedBatches: number;
    deferred: Record<string, number>;
    fromMs: number;
    untilMs: number;
    evidenceCutoffMs: number;
  } | null;
}
export type ManagementRecordKind = 'analysis' | 'optimization' | 'notification';
export type ManagementRecordPeriod = 'all' | 'today' | '7d';
export type ManagementRecordStatus =
  | 'all'
  | 'pending'
  | 'dispatching'
  | 'waiting'
  | 'retryable'
  | 'uncertain'
  | 'done'
  | 'blocked'
  | 'attention';
export interface ManagementRecordQuery {
  kind: ManagementRecordKind;
  period: ManagementRecordPeriod;
  status: ManagementRecordStatus;
  limit: 20 | 8;
  cursor?: string;
}
export interface ManagementRecordPage {
  jobs: ManagementJob[];
  nextCursor: string | null;
}
