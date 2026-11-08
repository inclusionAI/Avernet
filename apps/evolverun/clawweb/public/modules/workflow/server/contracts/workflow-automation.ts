import type { RepairBaseline } from './repair-batch.js';
import type { RepairSelectionRequest, RepairWorkbenchService } from './repair-workbench.js';
export type MinimumImpact = 'medium' | 'high';
export type WorkflowAutomationPolicy = {
  workflowId: string; enabled: boolean; autoDraft: boolean; dailyTime: string; timezone: 'Asia/Shanghai';
  /** Shared databases have one executing environment per workflow; legacy policies belong to prod. */
  executionEnvironment?: 'pre' | 'prod';
  minimumImpact?: MinimumImpact; scopeVersion?: 'legacy' | 'all-qualified/v1';
  notificationRecipients?: string[]; notificationVersion?: number;
  updatedAtMs: number; version: number; dailyConfigured: boolean; source: 'database' | 'environment' | 'default';
};
export type AutomationPolicyWrite = { enabled: boolean; autoDraft: boolean; dailyTime: string; expectedVersion: number; executionEnvironment?: 'pre' | 'prod'; minimumImpact?: MinimumImpact; dailyConfigured?: boolean; notificationRecipients?: string[]; expectedNotificationVersion?: number };
export type AutomationOrigin = { kind: 'daily'; intentId: string; policyVersion: number; evidenceCutoffMs: number; roundId?: string; batchIndex?: number; batchCount?: number; totalItems?: number };
export type AnalysisObservation = { status: 'absent' | 'running' | 'completed' | 'failed' | 'uncertain'; analysisId?: string; complete?: boolean };
export type AutomationNotification = { key: string; workflowId: string; recipientId: string; title: string; text: string; path: string };
/** Host Plugin API. Unknown outcomes are distinct from confirmed rejection and never authorize blind replay. */
export interface WorkflowAutomationPorts {
  baseline(workflowId: string): Promise<RepairBaseline>;
  owner(workflowId: string): Promise<string | null>;
  canEdit(workflowId: string, actorId: string): Promise<boolean>;
  analysis: {
    /** Known workflow bounds historical JSON membership reads; optional for older Hosts. */
    lookup(flowId: string, workflowId?: string): Promise<AnalysisObservation>;
    start(input: { workflowId: string; flowId: string; actorId: string; policyVersion: number; dispatchToken: string; requestId: string }): Promise<AnalysisObservation>;
  };
  notify(input: AutomationNotification): Promise<{ status: 'sent' | 'retryable' | 'uncertain'; receipt?: string }>;
}
/** Read-only display fields; older journals may lack analysis/task locators. */
export type AutomationJobSummary = {
  id: string; kind: string; status: string; attempts: number; reason: string | null; taskId?: string;
  createdAtMs?: number; flowId?: string; analysisId?: string; complete?: boolean;
};
export type AutomationSnapshot = {
  schemaVersion: 'workflow-automation/v1'; workflowId: string; lastScannedAtMs: number | null; pauseReason: string | null;
  coverage: { total: number; pending: number; failed: number; partial: number; unknown: number };
  round?: { id: string; total: number; batches: number; completedBatches: number; deferred: Record<string, number>; fromMs: number; untilMs: number; evidenceCutoffMs: number };
  jobs: AutomationJobSummary[];
  /** Same eligibility and batch limit as retryFailures; independent of the recent-record limit. */
  retryableJobs?: AutomationJobSummary[];
};
export type AutomationRecordQuery = { kind?: 'all' | 'analysis' | 'optimization' | 'notification'; period?: 'all' | 'today' | '7d'; status?: 'all' | 'pending' | 'dispatching' | 'waiting' | 'retryable' | 'uncertain' | 'done' | 'blocked' | 'attention'; limit?: number; cursor?: string };
export type AutomationRecordPage = { jobs: AutomationJobSummary[]; nextCursor: string | null };
/** Service API for trusted scheduler/event adapters; browser delivery never invokes automatic publication. */
export interface WorkflowAutomationService {
  tick(): Promise<number>;
  onRunTerminal(input: { flowId: string; status: string }): Promise<void>;
  records(workflowId: string, query?: AutomationRecordQuery): Promise<AutomationRecordPage>;
  status(workflowId: string): Promise<AutomationSnapshot>;
  retryFailures(workflowId: string, jobId?: string): Promise<void>;
  authorizeDraft(input: { workflowId: string; actorId: string; taskId: string; origin: AutomationOrigin; itemIds: string[] }): Promise<void>;
}
export type AutomatedSelection = RepairSelectionRequest & { automation: AutomationOrigin };
export type AutomationWorkbench = Pick<RepairWorkbenchService, 'candidates' | 'item' | 'items' | 'create' | 'revise' | 'retryGeneration' | 'request' | 'task'>;

export class RepairAutomationDeniedError extends Error {}
