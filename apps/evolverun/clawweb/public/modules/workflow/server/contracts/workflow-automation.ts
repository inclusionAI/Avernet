import type { RepairBaseline } from './repair-batch.js';
import type { RepairSelectionRequest, RepairWorkbenchService } from './repair-workbench.js';
export type WorkflowAutomationPolicy = {
  workflowId: string; enabled: boolean; autoDraft: boolean; dailyTime: string; timezone: 'Asia/Shanghai';
  updatedAtMs: number; version: number; dailyConfigured: boolean; source: 'database' | 'environment' | 'default';
};
export type AutomationPolicyWrite = { enabled: boolean; autoDraft: boolean; dailyTime: string; expectedVersion: number };
export type AutomationOrigin = { kind: 'daily'; intentId: string; policyVersion: number; evidenceCutoffMs: number };
export type AnalysisObservation = { status: 'absent' | 'running' | 'completed' | 'failed' | 'uncertain'; analysisId?: string; complete?: boolean };
export type AutomationNotification = { key: string; workflowId: string; recipientId: string; title: string; text: string; path: string };
/** Host Plugin API. Unknown outcomes are distinct from confirmed rejection and never authorize blind replay. */
export interface WorkflowAutomationPorts {
  baseline(workflowId: string): Promise<RepairBaseline>;
  owner(workflowId: string): Promise<string | null>;
  canEdit(workflowId: string, actorId: string): Promise<boolean>;
  analysis: {
    lookup(flowId: string): Promise<AnalysisObservation>;
    start(input: { workflowId: string; flowId: string; actorId: string; policyVersion: number; dispatchToken: string; requestId: string }): Promise<AnalysisObservation>;
  };
  notify(input: AutomationNotification): Promise<{ status: 'sent' | 'retryable' | 'uncertain'; receipt?: string }>;
}
export type AutomationSnapshot = {
  schemaVersion: 'workflow-automation/v1'; workflowId: string; lastScannedAtMs: number | null; pauseReason: string | null;
  coverage: { total: number; pending: number; failed: number; partial: number; unknown: number };
  jobs: Array<{ id: string; kind: string; status: string; attempts: number; reason: string | null; taskId?: string }>;
};
/** Service API for trusted scheduler/event adapters; browser delivery never invokes automatic publication. */
export interface WorkflowAutomationService {
  tick(): Promise<number>;
  onRunTerminal(input: { flowId: string; status: string }): Promise<void>;
  status(workflowId: string): Promise<AutomationSnapshot>;
  retryFailures(workflowId: string): Promise<void>;
  authorizeDraft(input: { workflowId: string; actorId: string; taskId: string; origin: AutomationOrigin; itemIds: string[] }): Promise<void>;
}
export type AutomatedSelection = RepairSelectionRequest & { automation: AutomationOrigin };
export type AutomationWorkbench = Pick<RepairWorkbenchService, 'candidates' | 'create' | 'request' | 'task'>;

export class RepairAutomationDeniedError extends Error {}
