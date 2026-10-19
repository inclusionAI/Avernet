import type { RepairBaseline } from './repair-batch.js';
export type RepairTrialTarget = { id: string; botId: string; ownerId: string; environment: 'prod'; label: string;
  executionMode?: 'pinned' | 'saved' | 'unavailable'; unavailableReason?: string; capabilityId?: string };
export type RepairTrialCandidate = { workflowId: string; taskId: string; revision: number; candidateCommit: string; packDigest: string; baseline: RepairBaseline };
export type RepairTrialOptions = { source?: 'saved'; packId: string; workflowIds?: string[]; candidateRef: string; inputSchema: Record<string, unknown> | null;
  inputDefinition?: 'defined' | 'none' | 'missing' };
export type RepairTrialStatus = 'dispatching' | 'awaiting_run' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'unknown';
export type RepairTrial = RepairTrialCandidate & {
  mode?: 'pinned' | 'saved';
  id: string; flowId: string | null; requestId: string; actorId: string; target: RepairTrialTarget;
  stateVersion: number; createdAtMs: number; updatedAtMs: number; status: RepairTrialStatus;
  input: Record<string, unknown>; inputDigest: string; inputSchemaDigest: string; options: RepairTrialOptions;
  error: string | null; output?: string; durationMs?: number | null;
};
/** mode is retained only for old clients/idempotent reads; it never selects execution. */
export type RepairTrialRequest = { mode?: 'pinned' | 'saved'; revision: number; expectedStateVersion: number; requestId: string;
  targetId: string; input: Record<string, unknown>; inputSchemaDigest: string };
/** Host boundary: permissions and installed capabilities belong to the Host. */
export interface RepairTrialPort {
  targets(workflowId: string, actorId: string): Promise<RepairTrialTarget[]>;
  /** Read-only capability negotiation for the selected Bot. Missing support fails closed. */
  prepare?(candidate: RepairTrialCandidate, actorId: string, targetId: string): Promise<RepairTrialTarget>;
  inspect(candidate: RepairTrialCandidate, mode?: 'saved'): Promise<RepairTrialOptions>;
  /** Pin exact candidate in isolated immutable Git ref. Idempotent; no deploy tag/active pointer writes. */
  save(candidate: RepairTrialCandidate, options: RepairTrialOptions): Promise<void>;
  /** Recheck permission/capability identity, then dispatch the existing run selector once. Never fallback/auto-retry. */
  dispatch(trial: RepairTrial): Promise<{ status: 'accepted' | 'unknown' | 'failed'; error?: string }>;
}
