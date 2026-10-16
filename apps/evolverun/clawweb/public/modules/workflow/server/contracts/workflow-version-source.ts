/** Read-only provenance. Only a verified publication receipt may identify an optimization. */
export type WorkflowVersionSource = {
  kind: 'auto_optimization' | 'manual_optimization' | 'rollback' | 'unknown';
  deployNumber: number;
  baselineDeployNumber: number | null;
  taskId: string | null;
  revision: number | null;
  automationRoundId: string | null;
  actorId: string | null;
  /** Initial successful activation time; never inferred from task completion or tag creation. */
  effectiveAtMs: number | null;
  restoredFromDeployNumber: number | null;
  restoredSource?: WorkflowVersionSource;
};
export interface WorkflowVersionSourceService {
  forWorkflow(workflowId: string): Promise<Map<number, WorkflowVersionSource>>;
  getSource(workflowId: string, deployNumber: number): Promise<WorkflowVersionSource>;
}
