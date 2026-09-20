/** Runtime dependency supplied by the Host. Public modules never import its internal implementation. */
export type AisStatus = "running" | "success" | "failed" | "stopped";
export type AisJobStatusDetail = { status: AisStatus; rawStatus: string; errorMessage: string | null };
export interface AisExecutor {
  execute(userId: string, globalParam: Record<string, string>, snapshotId?: number): Promise<string>;
  /** Optional platform link for operators; business code never constructs provider URLs. */
  jobUrl?(jobId: string): string | null;
  getJobStatus(jobId: string): Promise<AisStatus>;
  getJobStatusDetail(jobId: string): Promise<AisJobStatusDetail>;
  stopExecution(jobId: string): Promise<void>;
}
/** Deployment values are resolved by the Host, never supplied by public HTTP callers. */
export type SessionAisOptions = {
  configurationError?: "SESSION_AIS_CONFIG_INVALID";
  legacySnapshotId: number;
  deadlineSeconds: number;
  deployment?: { snapshotId: number; packageId: string };
};
