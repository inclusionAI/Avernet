import type { RepairBaseline } from './repair-batch.js';

export type RepairReleaseTarget = { id: string; botId: string; ownerId: string; environment: string; label: string };
export type RepairApproval = {
  id: string; workflowId: string; taskId: string; revision: number; stateVersion: number;
  requestId: string; actorId: string; candidateCommit: string; packDigest: string;
  candidateDigest: string; checksDigest: string; baselineDeployNumber: number;
  baseline: RepairBaseline; target: RepairReleaseTarget; acknowledgeUncoveredChecks: boolean;
  status: 'approved' | 'revoked'; createdAtMs: number;
};
export type PublicationReceipt = {
  requestId: string; candidateCommit: string; packDigest: string; deployNumber: number;
  releaseVersion: number; tagName: string; targetId: string;
};
export type RepairDeployment = {
  id: string; workflowId: string; taskId: string; revision: number; requestId: string;
  actorId: string; approvalId: string; candidateCommit: string; packDigest: string;
  baselineDeployNumber: number; baseline: RepairBaseline; target: RepairReleaseTarget;
  status: 'created' | 'dispatching' | 'uncertain' | 'running' | 'failed' | 'succeeded';
  stateVersion: number; receipt: PublicationReceipt | null; error: string | null;
  createdAtMs: number; updatedAtMs: number;
};
export type PublicationStatus = { status: 'running' | 'uncertain' | 'failed'; error?: string }
  | { status: 'succeeded'; receipt: PublicationReceipt };
/** Host Plugin API: freeze an authorized target; publish/reconcile the exact trusted identity. */
export interface RepairPublicationPort {
  targets(workflowId: string, actorId: string): Promise<RepairReleaseTarget[]>;
  activeBaseline(workflowId: string): Promise<RepairBaseline>;
  dispatch(deployment: RepairDeployment): Promise<PublicationStatus>;
  reconcile(deployment: RepairDeployment): Promise<PublicationStatus>;
  resume?(deployment: RepairDeployment): Promise<PublicationStatus>;
  stop?(deployment: RepairDeployment): Promise<PublicationStatus>;
}
export type RepairConfirmRequest = {
  revision: number; expectedStateVersion: number; requestId: string; targetId: string;
  acknowledgeUncoveredChecks: boolean;
};
export type RepairDeployRequest = { revision: number; expectedApprovalVersion: number; requestId: string };
export type RepairReleaseDetail = {
  approval: RepairApproval | null; deployments: RepairDeployment[];
};
/** Service API; routes enforce authenticated workflow access in addition to target checks. */
export interface RepairReleaseService {
  readonly configured: boolean;
  detail(taskId: string): Promise<RepairReleaseDetail>;
  targets(actorId: string, taskId: string): Promise<RepairReleaseTarget[]>;
  confirm(actorId: string, taskId: string, request: RepairConfirmRequest): Promise<RepairApproval>;
  revoke(actorId: string, taskId: string, request: RepairDeployRequest): Promise<RepairApproval>;
  deploy(actorId: string, taskId: string, request: RepairDeployRequest): Promise<RepairDeployment>;
  reconcile(actorId: string, taskId: string, deploymentId: string): Promise<RepairDeployment>;
  resume(actorId: string, taskId: string, deploymentId: string, requestId: string): Promise<RepairDeployment>;
  stop(actorId: string, taskId: string, deploymentId: string, requestId: string): Promise<RepairDeployment>;
}
