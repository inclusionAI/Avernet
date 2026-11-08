export type AgentTarget = {
  environment: "pre" | "prod";
  ownerId: string;
  botId: string;
  botType: "personal" | "service";
  runtimeIdentity?: import("@avernet/clawweb-shared/server/services/bot-runtime").BotRuntimeIdentity;
  deviceUuid?: string; providerDeviceId?: string;
  botStatus: string | null;
  bindingId: string;
  bindingStatus: string | null;
  entityId?: string;
  entityType?: string;
  activeEngine?: string;
  provider: string;
  deviceId: string;
  /** Present only for a directly reachable legacy ARCA binding. */
  sandboxId?: string;
  /** Raw ARCA PaaS instance id used to sign the proxypass target. */
  arcaInstanceId?: string;
  observedAt: string;
  source: "clawweb_runtime_catalog" | "ocb_backend_current";
};

export type AgentRuntimeTargetSnapshot = {
  version: number;
  fingerprint: string;
  target: AgentTarget;
  /** `ocb_context` is retained only for previously persisted snapshots. */
  reason: "task_created" | "browser_relay" | "ocb_context" | "before_action" | "after_action" | "resume";
};

export type AgentPlanAction = {
  actionId: string;
  type: "container_command" | "ocb_operation" | "engine_operation";
  engineOperation?: import("@avernet/clawweb-shared/server/services/bot-runtime").EngineOperation;
  summary: string;
  risk: string;
  verification: string;
  rollback: string | null;
  dependsOn?: string[];
  rollbackActionId?: string | null;
  command?: string;
  operation?: { type: string; params?: Record<string, unknown> };
};

export type AgentLogIdentifier = "botId" | "ownerId" | "traceId" | "relatedTaskId" | "errorText";
export type AgentDiscoveredIdentifierKind = "bindingId" | "publishId" | "deviceUuid" | "sessionId" | "traceId" | "taskId";
export type AgentDiscoveredIdentifierCandidate = { kind: AgentDiscoveredIdentifierKind; value: string };
export type AgentDiscoveredIdentifier = AgentDiscoveredIdentifierCandidate & { evidenceId: string };

export type AgentLogSearchInput = {
  clientRequestId?: unknown;
  purpose?: unknown;
  identifiers?: AgentLogIdentifier[];
  discoveredIdentifiers?: AgentDiscoveredIdentifier[];
  sources?: string[];
  from?: number;
  to?: number;
  limit?: number;
};

export type AgentRuntimeInspectInput = ({ clientRequestId?: unknown; purpose?: unknown } & (
  | { operation: "trajectory_read"; sessionKey: string; offset?: number; limit?: number; traceId?: string }
  | { operation: "engine_read"; request: import("@avernet/clawweb-shared/server/services/bot-runtime").EngineOperation }
  | { operation: "fs_list"; path: string; maxEntries?: number }
  | { operation: "fs_find"; path: string; name: string; maxDepth?: number; maxEntries?: number }
  | { operation: "fs_stat"; path: string }
  | { operation: "fs_read"; path: string; startLine?: number; lines?: number }
  | { operation: "fs_search"; path: string; pattern: string; matchMode?: "literal" | "regex"; maxMatches?: number }
  | { operation: "process_list"; pattern?: string }
  | { operation: "port_list" }
  | { operation: "http_get"; port: number; path: string }
  | { operation: "shell_exec"; command: string }
));

export type AgentToolContext = {
  taskId: string;
  target: AgentTarget;
  issue: {
    traceId: string | null;
    relatedTaskId: string | null;
    errorText: string | null;
    timeRange: { from: number; to: number };
  };
};
