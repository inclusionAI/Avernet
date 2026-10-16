import type { RepairBaseline } from './repair-batch.js';
export type RepairTrialTarget = { id: string; botId: string; ownerId: string; environment: 'prod'; label: string };
export type RepairTrialCandidate = { workflowId: string; taskId: string; revision: number; candidateCommit: string; packDigest: string; baseline: RepairBaseline };
export type RepairTrialOptions = { packId: string; workflowIds?: string[]; candidateRef: string; inputSchema: Record<string, unknown> | null };
export type RepairTrialStatus = 'dispatching' | 'awaiting_run' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'unknown';
export type RepairTrial = RepairTrialCandidate & {
  id: string; flowId: string | null; requestId: string; actorId: string; target: RepairTrialTarget;
  stateVersion: number; createdAtMs: number; updatedAtMs: number; status: RepairTrialStatus;
  input: Record<string, unknown>; inputDigest: string; inputSchemaDigest: string; options: RepairTrialOptions;
  error: string | null; output?: string; durationMs?: number | null;
};
export type RepairTrialRequest = { revision: number; expectedStateVersion: number; requestId: string;
  targetId: string; input: Record<string, unknown>; inputSchemaDigest: string };
/** Host boundary: production, authorized and capable Bots only; no editable workspace or saved-latest execution. */
export interface RepairTrialPort {
  targets(workflowId: string, actorId: string): Promise<RepairTrialTarget[]>;
  inspect(candidate: RepairTrialCandidate): Promise<RepairTrialOptions>;
  /** Pin exact candidate in isolated immutable Git ref. Idempotent; no deploy tag/active pointer writes. */
  save(candidate: RepairTrialCandidate, options: RepairTrialOptions): Promise<void>;
  /** Only workflow/request identities enter the existing run command. Message acceptance is not workflow completion. Never auto-retry. */
  dispatch(trial: RepairTrial): Promise<{ status: 'accepted' | 'unknown' | 'failed' }>;
}
