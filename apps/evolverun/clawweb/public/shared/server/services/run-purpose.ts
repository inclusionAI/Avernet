import type { IDatabase } from "../db.js";

export type RunPurpose = "production" | "trial";

/** Only the server-owned release record identifies a candidate trial. Versions are not purpose markers. */
export function trialRunSql(flowIdExpression: string): string {
  return `EXISTS (SELECT 1 FROM workflow_repair_release_records trial WHERE trial.record_kind IN ('trial', 'trial_run') AND trial.record_id = ${flowIdExpression})`;
}

export function productionRunSql(flowIdExpression: string): string {
  return `NOT ${trialRunSql(flowIdExpression)}`;
}

export async function isTrialRun(db: IDatabase, flowId: string): Promise<boolean> {
  const rows = await db.query<{ record_id: string }>(
    "SELECT record_id FROM workflow_repair_release_records WHERE record_kind IN ('trial', 'trial_run') AND record_id = ? LIMIT 1", [flowId]);
  return rows.length > 0;
}

/** Relation for official analytics; callers must supply the outer SQL alias. */
export function productionRunsSql(): string {
  return `(SELECT * FROM flow_runs WHERE ${productionRunSql("flow_runs.flow_id")})`;
}

export function productionNodesSql(): string {
  return `(SELECT * FROM node_executions WHERE ${productionRunSql("node_executions.flow_id")})`;
}

export function productionDiagnosesSql(): string {
  return `(SELECT * FROM workflow_healing_diagnoses WHERE ${productionRunSql("workflow_healing_diagnoses.flow_id")})`;
}
