/** A Bot ID is only unique within its owner. '*' is an explicit all-owner grant. */
export type RunViewBot = { botId: string; ownerId: string };

export type RunViewScope = "all" | "deny" | { bots: RunViewBot[] };

/** Supplied by the host with its configured Bot directory and permission policy. */
export interface RunViewPermissions {
  /** Resolve readable direct and inherited grants together, without per-workflow queries. */
  listRunViewScopes(userId: string, workflowId?: string): Promise<Map<string, RunViewScope>>;
}
