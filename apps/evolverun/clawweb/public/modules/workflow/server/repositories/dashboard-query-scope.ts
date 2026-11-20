import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export interface DashboardWorkflowFilter {
  readonly restricted: boolean;
  readonly params: readonly string[];
  /** SQL for an existing WHERE clause; only accept static column references. */
  and(column?: "workflow_id" | "d.workflow_id" | "ne.workflow_id" | "ws.workflow_id"): string;
}

export function dashboardWorkflowFilter(
  dbType: IDatabase["dbType"],
  workflowIds: readonly string[] | null,
): DashboardWorkflowFilter {
  const params = [...new Set(workflowIds ?? [])];
  const placeholders = params.map(() => "?").join(", ");
  const restricted = workflowIds !== null;
  return {
    restricted,
    params,
    and(column = "workflow_id") {
      if (!restricted) return "";
      if (params.length === 0) return " AND 1 = 0";
      // The ACL grants exact IDs, even if a source table's collation is case-insensitive.
      const key = dbType === "mysql" || dbType === "zdas" ? `CAST(${column} AS BINARY)` : `${column} COLLATE BINARY`;
      return ` AND ${key} IN (${placeholders})`;
    },
  };
}
