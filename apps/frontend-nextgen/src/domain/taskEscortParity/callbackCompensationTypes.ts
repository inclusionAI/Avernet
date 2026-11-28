/** The callback-compensation boundary contains only server-sanitized display data. */
export interface CallbackCompensationScope {
  workflowId: string;
  environment: string;
}
export interface CallbackCompensationReceiver {
  configId: string;
  name: string;
  receiverId?: string;
  targetOrigin: string;
}
export interface CallbackCompensationCapabilities extends CallbackCompensationScope {
  canPreview: true;
  executionEnabled: boolean;
  blockedReasons: string[];
  configs: CallbackCompensationReceiver[];
}
export type CallbackCompensationEligibility = 'eligible' | 'delivered' | 'review' | 'ineligible';
export interface CallbackCompensationPreviewItem {
  itemId: string;
  flowId: string;
  status: CallbackCompensationEligibility;
  reasons: string[];
  payload: unknown | null;
}
export interface CallbackCompensationPreview extends CallbackCompensationScope {
  previewId: string;
  receiver: CallbackCompensationReceiver;
  expiresAt: string;
  items: CallbackCompensationPreviewItem[];
  summary: Record<CallbackCompensationEligibility, number>;
}
export interface CallbackCompensationRange {
  configId: string;
  from?: string;
  to?: string;
  flowIds?: string[];
}
export interface CallbackCompensationCreate {
  previewId: string;
  itemIds: string[];
  reason: string;
  requestKey: string;
}
export type CallbackCompensationItemState = 'queued' | 'sending' | 'received' | 'failed' | 'unknown' | 'skipped';
export interface CallbackCompensationBatchItem {
  itemId: string;
  flowId: string;
  state: CallbackCompensationItemState;
  reason?: string;
  httpStatus?: number;
  auditPending: boolean;
}
export interface CallbackCompensationBatch extends CallbackCompensationScope {
  batchId: string;
  status: 'queued' | 'running' | 'stopped' | 'completed';
  items: CallbackCompensationBatchItem[];
  counts: Record<string, number>;
}
