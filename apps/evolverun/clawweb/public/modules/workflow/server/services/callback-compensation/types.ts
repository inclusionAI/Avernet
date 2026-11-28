/** Library-style Service API. Only the HTTP adapter owns administrator authentication/CSRF.
 * Actor IDs must come from that trusted adapter, never from a request body.
 */
export class CompensationError extends Error {
  constructor(public readonly code: string, public readonly status: number, message = code) {
    super(message); this.name = 'CompensationError';
  }
}
export interface ReceiverDto { configId: string; name: string; receiverId?: string; targetOrigin: string }
export interface CapabilitiesDto {
  workflowId: string; environment: string; canPreview: true; executionEnabled: boolean;
  blockedReasons: string[]; configs: ReceiverDto[];
}
export interface PreviewInput { configId: string; from?: string; to?: string; flowIds?: string[] }
export type Eligibility = 'eligible' | 'delivered' | 'review' | 'ineligible';
export interface PreviewItemDto {
  itemId: string; flowId: string; status: Eligibility; reasons: string[]; payload: unknown | null;
}
export interface PreviewDto {
  previewId: string; workflowId: string; environment: string; receiver: ReceiverDto; expiresAt: string;
  items: PreviewItemDto[]; summary: Record<Eligibility, number>;
}
export interface CreateInput { previewId: string; itemIds: string[]; reason: string; requestKey: string }
export type ItemState = 'queued' | 'sending' | 'received' | 'failed' | 'unknown' | 'skipped';
export interface BatchItemDto {
  itemId: string; flowId: string; state: ItemState; reason?: string; httpStatus?: number; auditPending: boolean;
}
export interface BatchDto {
  batchId: string; workflowId: string; environment: string;
  status: 'queued' | 'running' | 'stopped' | 'completed'; items: BatchItemDto[]; counts: Record<string, number>;
}
/** Server-only provider input: snapshotHash covers the authoritative run AND all node attempts.
 * retryEpoch is only a comparison fence, NOT a generation. No browser approval flags exist.
 */
export interface EvidenceQuery {
  environment: string; workflowId: string; flowId: string; configId: string;
  snapshotHash: string; retryEpoch: number; completedAt: string; now: string;
}
/** Reviewed, exact per-run authorization, supplied by a trusted server-only manifest/provider.
 * The provider must establish historical routing, full receiver alias history, independent
 * generation attestation, and Cortex business deduplication spanning legacy and late requests.
 * References are reviewable immutable evidence IDs, not booleans or user-entered approvals.
 */
export interface CompensationEvidence {
  environment: string; workflowId: string; flowId: string; receiverId: string;
  generation: string; retryEpoch: number; snapshotHash: string;
  generationEvidenceRef: string;
  configIds: string[]; urls: string[]; url: string;
  historicalRoute: { evidenceRef: string; validFrom: string; validUntil: string };
  legacyIdempotency: {
    evidenceRef: string; keyContract: string; legacySenderContract: string;
    generationContract: string; lateArrivalContract: string;
    earliestCompletion: string; latestCompletion: string; retentionUntil: string; lateArrivalUntil: string;
  };
  evidenceRef: string; validFrom: string; validUntil: string; phaseNullable: boolean;
}
/** No default authorization. A missing provider/evidence leaves successful runs in review. */
export interface CompensationEvidenceProvider {
  resolve(query: EvidenceQuery): Promise<CompensationEvidence | null>;
}
export interface CompensationTransportRequest {
  url: string; rawBody: string; headers: Record<string, string>; timeoutMs: number;
  redirect: 'error'; signal: AbortSignal;
}
/** Exactly one attempt; implementations MUST honor signal/redirect, never retry or follow redirects.
 * Response body and headers are deliberately not collected or persisted.
 */
export type CompensationTransport = (request: CompensationTransportRequest) => Promise<{ status: number }>;
export interface CompensationOptions {
  environment: string; protectionKey?: Buffer; evidence?: CompensationEvidenceProvider;
  transport: CompensationTransport; now?: () => number;
}
export interface CallbackCompensationServiceApi {
  capabilities(workflowId: string): Promise<CapabilitiesDto>;
  preview(workflowId: string, actorId: string, input: PreviewInput): Promise<PreviewDto>;
  create(workflowId: string, actorId: string, input: CreateInput): Promise<BatchDto>;
  get(workflowId: string, actorId: string, batchId: string): Promise<BatchDto>;
  stop(workflowId: string, actorId: string, batchId: string): Promise<BatchDto>;
  tick(): Promise<void>;
  close(): Promise<void>;
  shutdown(): Promise<void>;
}
