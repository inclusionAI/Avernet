import type { IDatabase } from "@avernet/clawweb-shared/server/db";

/** ID comparisons must not inherit a case-insensitive or space-padding collation. */
export function exactRunKey(db: IDatabase, column: string): string {
  return db.dbType === "sqlite" ? `${column} COLLATE BINARY = ?` : `BINARY ${column} = BINARY ?`;
}

/** Build a read predicate for evidence that belongs to a unique flow_id. Hosts
 * can also pin the authoritative workflow_id they have already authorized. */
export function runFlowWhere(db: IDatabase, flowId: string, workflowId?: string): [string, string[]] {
  const exactFlow = `flow_id = ? AND ${exactRunKey(db, "flow_id")}`;
  if (workflowId === undefined) return [exactFlow, [flowId, flowId]];
  return [`${exactFlow} AND EXISTS (SELECT 1 FROM flow_runs WHERE ${exactFlow} AND ${exactRunKey(db, "workflow_id")})`,
    [flowId, flowId, flowId, flowId, workflowId]];
}

/** A node/event's legacy NULL (not empty or mismatched) workflow may inherit
 * from the exact unique flow. Check before pagination and selecting attempts. */
export function runEvidenceWhere(db: IDatabase, flowId: string, workflowId?: string): [string, string[]] {
  const [where, params] = runFlowWhere(db, flowId, workflowId);
  if (workflowId === undefined) return [where, params];
  return [`${where} AND (workflow_id IS NULL OR ${exactRunKey(db, "workflow_id")})`, [...params, workflowId]];
}
