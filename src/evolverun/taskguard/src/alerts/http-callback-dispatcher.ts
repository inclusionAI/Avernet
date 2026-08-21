/**
 * HttpCallbackDispatcher — sends HTTP POST callbacks to external subsystems
 * when workflow lifecycle events occur.
 *
 * Designed to be called in fire-and-forget mode from Controller's
 * emitNodeEvent() and completeFlowRun(), exactly like WorkflowNotificationDispatcher.
 *
 * Config sources:
 *  1. YAML `notifications.httpCallbacks` — declared in the workflow spec
 *  2. DB `http_callback_configs` — managed via clawweb UI (overrides YAML by name)
 *
 * All dispatch methods are async but should be called with `.catch(() => {})`
 * so that callback failures never block workflow state transitions.
 */
import type { NodeLifecycleEvent, NodeLifecyclePayload } from "../controller-hooks/types.js";
import type {
  HttpCallbackConfig,
  HttpCallbackPayload,
  HttpCallbackDispatchResult,
  NotifyEvent,
  ExtInfo,
  FlowRunSnapshot,
  NodeExecutionSnapshot,
} from "./http-callback-types.js";
import type { IFlowRunRepository, INodeExecutionRepository } from "../db/repositories/types.js";
import type { IHttpCallbackLogRepository } from "./http-callback-types.js";
import { signPayload } from "./http-callback-signer.js";

/** Canonical list of valid NotifyEvent values, used for DB row validation. */
const VALID_NOTIFY_EVENTS: NotifyEvent[] = [
  "workflow_started", "node_started", "node_succeeded",
  "node_failed", "node_rejected", "node_skipped",
  "workflow_succeeded", "workflow_failed", "workflow_cancelled",
];

/** Map NodeLifecycleEvent to the corresponding NotifyEvent, or null if not mappable. */
function mapNodeEvent(event: NodeLifecycleEvent): NotifyEvent | null {
  const mapping: Partial<Record<NodeLifecycleEvent, NotifyEvent>> = {
    node_started: "node_started",
    node_succeeded: "node_succeeded",
    node_failed: "node_failed",
    node_rejected: "node_rejected",
    node_skipped: "node_skipped",
  };
  return mapping[event] ?? null;
}

export class HttpCallbackDispatcher {
  private flowRunRepo: IFlowRunRepository | null;
  private nodeExecRepo: INodeExecutionRepository | null;
  private logRepo: IHttpCallbackLogRepository | null;
  /** workflowId → resolved configs (merged from YAML + DB) */
  private configCache: Map<string, HttpCallbackConfig[]>;
  /** Max KB for request_body / response_body stored in audit log. */
  private logMaxIoKb: number;
  /** Whether audit logging is enabled. */
  private logEnabled: boolean;

  constructor(deps: {
    flowRunRepo: IFlowRunRepository | null;
    nodeExecRepo: INodeExecutionRepository | null;
    logRepo?: IHttpCallbackLogRepository | null;
    configs: Map<string, HttpCallbackConfig[]>;
    logMaxIoKb?: number;
    logEnabled?: boolean;
  }) {
    this.flowRunRepo = deps.flowRunRepo;
    this.nodeExecRepo = deps.nodeExecRepo;
    this.logRepo = deps.logRepo ?? null;
    this.configCache = deps.configs;
    this.logMaxIoKb = deps.logMaxIoKb ?? 10;
    this.logEnabled = deps.logEnabled ?? true;
  }

  /**
   * Update the config cache (called when DB configs change at runtime).
   */
  updateConfigs(configs: Map<string, HttpCallbackConfig[]>): void {
    this.configCache = configs;
  }

  /** Check whether the config cache already has entries for a workflow. */
  hasConfigForWorkflow(workflowId: string): boolean {
    return this.configCache.has(workflowId);
  }

  /** Add or replace configs for a single workflow (hot-load). */
  addConfigsForWorkflow(workflowId: string, configs: HttpCallbackConfig[]): void {
    this.configCache.set(workflowId, configs);
  }

  /**
   * Dispatch a node-level lifecycle event.
   * Called from emitNodeEvent() in controller.ts.
   */
  async dispatchNodeEvent(
    event: NodeLifecycleEvent,
    payload: NodeLifecyclePayload,
  ): Promise<void> {
    const notifyEvent = mapNodeEvent(event);
    if (!notifyEvent) return; // Not a mappable event (e.g., node_retry, node_progress)

    const configs = this.getEnabledConfigs(payload.workflowId, notifyEvent);
    if (configs.length === 0) {
      const cachedWorkflowIds = [...this.configCache.keys()].join(",");
      console.warn(`[http-callback] dispatchNodeEvent: NO matching configs for workflow=${payload.workflowId} event=${notifyEvent} (cacheSize=${this.configCache.size} cachedWorkflowIds=[${cachedWorkflowIds}])`);
      return;
    }
    console.log(`[http-callback] dispatchNodeEvent: workflow=${payload.workflowId} flowId=${payload.flowId} node=${payload.nodeId} event=${notifyEvent} matchedConfigs=${configs.length}`);

    // Build ext_info per-config to respect includeNodeOutput privacy:
    // a config with includeNodeOutput=false should never receive node output
    for (const config of configs) {
      if (!config.notifyOn.includes(notifyEvent)) continue;

      const extInfo = await this.buildExtInfo(payload.flowId, payload.workflowId, config.includeNodeOutput, false, true);

      const callbackPayload: HttpCallbackPayload = {
        workflow_id: payload.workflowId,
        flow_id: payload.flowId,
        status: notifyEvent,
        ext_info: extInfo,
      };

      // Fire and forget — errors are logged inside sendCallback
      void this.sendCallback(config, callbackPayload, {
        flowId: payload.flowId,
        workflowId: payload.workflowId,
        notifyEvent,
        nodeId: payload.nodeId,
      }).catch((err) => {
        console.warn(
          `[http-callback] dispatchNodeEvent failed: workflow=${payload.workflowId} ` +
          `node=${payload.nodeId} event=${notifyEvent} url=${config.url} ` +
          `error=${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }
  }

  /**
   * Dispatch a workflow-level started event.
   * Called from handleRun() after the workflow is launched.
   */
  async dispatchWorkflowStarted(
    workflowId: string,
    flowId: string,
  ): Promise<void> {
    const configs = this.getEnabledConfigs(workflowId, "workflow_started");
    if (configs.length === 0) {
      const cachedWorkflowIds = [...this.configCache.keys()].join(",");
      console.warn(`[http-callback] dispatchWorkflowStarted: NO matching configs for workflow=${workflowId} (cacheSize=${this.configCache.size} cachedWorkflowIds=[${cachedWorkflowIds}])`);
      return;
    }
    console.log(`[http-callback] dispatchWorkflowStarted: workflow=${workflowId} flowId=${flowId} matchedConfigs=${configs.length}`);

    for (const config of configs) {
      if (!config.notifyOn.includes("workflow_started")) continue;

      // waitForFlowRunInsert=true: retry DB queries if flowRunRow is null,
      // to handle the race where the flow_runs INSERT hasn't committed yet.
      const extInfo = await this.buildExtInfo(flowId, workflowId, config.includeNodeOutput, false, true);

      const callbackPayload: HttpCallbackPayload = {
        workflow_id: workflowId,
        flow_id: flowId,
        status: "started",
        ext_info: extInfo,
      };

      void this.sendCallback(config, callbackPayload, {
        flowId,
        workflowId,
        notifyEvent: "workflow_started",
      }).catch((err) => {
        console.warn(
          `[http-callback] dispatchWorkflowStarted failed: workflow=${workflowId} ` +
          `flowId=${flowId} url=${config.url} ` +
          `error=${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }
  }

  /**
   * Dispatch a workflow-level completion event (succeeded or failed).
   * Called from completeFlowRun() in controller.ts.
   */
  async dispatchWorkflowEvent(
    workflowId: string,
    flowId: string,
    status: "succeeded" | "failed" | "cancelled",
  ): Promise<void> {
    const notifyEvent: NotifyEvent = status === "succeeded"
      ? "workflow_succeeded"
      : status === "cancelled"
        ? "workflow_cancelled"
        : "workflow_failed";
    const configs = this.getEnabledConfigs(workflowId, notifyEvent);
    if (configs.length === 0) {
      const cachedWorkflowIds = [...this.configCache.keys()].join(",");
      console.warn(`[http-callback] dispatchWorkflowEvent: NO matching configs for workflow=${workflowId} event=${notifyEvent} (cacheSize=${this.configCache.size} cachedWorkflowIds=[${cachedWorkflowIds}])`);
      return;
    }
    console.log(`[http-callback] dispatchWorkflowEvent: workflow=${workflowId} flowId=${flowId} status=${status} matchedConfigs=${configs.length}`);

    for (const config of configs) {
      if (!config.notifyOn.includes(notifyEvent)) continue;

      // waitForNodeCompletion=true: retry DB queries if any node is still "running",
      // to handle the race where emitNodeEvent's fire-and-forget updateCompletionByFlowNode
      // hasn't committed to the DB yet when we query for ext_info.
      const extInfo = await this.buildExtInfo(flowId, workflowId, config.includeNodeOutput, true);

      const callbackPayload: HttpCallbackPayload = {
        workflow_id: workflowId,
        flow_id: flowId,
        status,
        ext_info: extInfo,
      };

      void this.sendCallback(config, callbackPayload, {
        flowId,
        workflowId,
        notifyEvent,
      }).catch((err) => {
        console.warn(
          `[http-callback] dispatchWorkflowEvent failed: workflow=${workflowId} ` +
          `flowId=${flowId} status=${status} url=${config.url} ` +
          `error=${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }
  }

  // ── Internal helpers ─────────────────────────────────────

  /**
   * Get enabled configs for a workflow that include the given event in their notifyOn.
   */
  private getEnabledConfigs(workflowId: string, event: NotifyEvent): HttpCallbackConfig[] {
    const configs = this.configCache.get(workflowId);
    if (!configs) return [];
    return configs.filter((c) => c.enabled && c.notifyOn.includes(event));
  }

  /**
   * Build the ext_info payload by querying flow_runs and node_executions.
   * Falls back to null/empty on DB errors — never throws.
   *
   * When `waitForNodeCompletion` is true (used for workflow completion events),
   * this method retries up to 3 times with 300ms delays if any node is still
   * "running" — this handles the race where emitNodeEvent's fire-and-forget
   * updateCompletionByFlowNode hasn't committed to the DB yet.
   *
   * When `waitForFlowRunInsert` is true (used for workflow started events),
   * this method retries up to 3 times with 200ms delays if flowRunRow is null —
   * this handles the race where the flow_runs INSERT hasn't committed yet.
   */
  private async buildExtInfo(
    flowId: string,
    workflowId: string,
    includeNodeOutput: boolean,
    waitForNodeCompletion = false,
    waitForFlowRunInsert = false,
  ): Promise<ExtInfo> {
    // Only retry when we actually have a flowRunRepo — if the repo is null,
    // retrying will never find the row and just wastes 600ms per event.
    const maxRetries = (waitForNodeCompletion || waitForFlowRunInsert) && this.flowRunRepo
      ? 3
      : 0;
    const retryDelayMs = waitForFlowRunInsert ? 200 : 300;

    let flowRunRow: Record<string, unknown> | null = null;
    let nodeExecRows: Array<Record<string, unknown>> = [];

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      const flowRunsPromise = this.flowRunRepo
        ? this.flowRunRepo.findByFlowId(flowId).catch(() => null)
        : Promise.resolve(null);

      const nodeExecsPromise = this.nodeExecRepo
        ? this.nodeExecRepo.findByFlowId(flowId).catch(() => [])
        : Promise.resolve([]);

      const [fr, ne] = await Promise.all([flowRunsPromise, nodeExecsPromise]);
      flowRunRow = fr as Record<string, unknown> | null;
      nodeExecRows = (ne ?? []) as Array<Record<string, unknown>>;

      // Check if we need to retry
      // When waitForNodeCompletion is true (workflow completion events), also
      // check that flow_runs.status is no longer "running" — this catches the
      // race where updateCompletion hasn't committed yet even though the caller
      // already invoked completeFlowRun("succeeded"|"failed").
      const needsRetry = waitForNodeCompletion
        ? nodeExecRows.some((r) => r.status === "running") ||
          (flowRunRow != null && flowRunRow.status === "running")
        : waitForFlowRunInsert
          ? !flowRunRow
          : false;

      if (!needsRetry) {
        if (attempt > 0) {
          console.log(`[http-callback] buildExtInfo: data settled after ${attempt} retry(s) flowId=${flowId}`);
        }
        break;
      }
      if (attempt < maxRetries) {
        const reason = waitForNodeCompletion ? "nodes still running or flow_runs.status=running" : "flowRun not found";
        console.log(`[http-callback] buildExtInfo: retrying (${reason}) flowId=${flowId} attempt=${attempt + 1}/${maxRetries}`);
        await sleep(retryDelayMs);
      }
    }

    if (!flowRunRow) {
      console.warn(`[http-callback] buildExtInfo: flowRun not found for flowId=${flowId} (flowRunRepo=${this.flowRunRepo ? "yes" : "no"})`);
    }
    const runningCount = nodeExecRows.filter((r) => r.status === "running").length;
    console.log(`[http-callback] buildExtInfo: flowId=${flowId} flowRun=${flowRunRow ? `status=${flowRunRow.status}` : "null"} nodeExecutions=${nodeExecRows?.length ?? 0}${runningCount > 0 ? ` running=${runningCount}` : ""}`);

    const fr = flowRunRow as Record<string, unknown> | null;
    const flowRuns: FlowRunSnapshot | null = fr
      ? {
          id: fr.id as number,
          flow_id: fr.flow_id as string,
          workflow_id: fr.workflow_id as string,
          workflow_title: fr.workflow_title as string | null,
          status: fr.status as string,
          params_json: fr.params_json as string | null,
          input_json: fr.input_json as string | null,
          result_json: fr.result_json as string | null,
          node_count: fr.node_count as number,
          succeeded_count: fr.succeeded_count as number,
          failed_count: fr.failed_count as number,
          total_duration_ms: fr.total_duration_ms as number | null,
          total_token_usage: fr.total_token_usage as number | null,
          triggered_by: fr.triggered_by as string | null,
          identity_key: fr.identity_key as string | null,
          current_phase: fr.current_phase as string | null,
          started_at: fr.started_at as number,
          completed_at: fr.completed_at as number | null,
          credentials_json: fr.credentials_json as string | null,
          origin_session_key: fr.origin_session_key as string | null,
          origin_session_id: fr.origin_session_id as string | null,
          origin_bot_id: fr.origin_bot_id as string | null,
          user_id: fr.user_id as string | null,
          plugin_version: fr.plugin_version as string | null,
          gmt_create: fr.gmt_create as number,
          gmt_modified: fr.gmt_modified as number | null,
        }
      : null;

    const nodeExecutions: NodeExecutionSnapshot[] = (nodeExecRows ?? []).map((row) => {
      const r = row as Record<string, unknown>;
      const snapshot: NodeExecutionSnapshot = {
        id: r.id as number,
        flow_id: r.flow_id as string,
        workflow_id: r.workflow_id as string,
        node_id: r.node_id as string,
        executor_type: r.executor_type as string | null,
        status: r.status as string,
        attempt: r.attempt as number,
        input_json: r.input_json as string | null,
        error_text: r.error_text as string | null,
        duration_ms: r.duration_ms as number | null,
        token_usage_json: r.token_usage_json as string | null,
        node_title: r.node_title as string | null,
        triggered_by: r.triggered_by as string | null,
        branch_id: r.branch_id as string | null,
        progress_message: r.progress_message as string | null,
        session_key: r.session_key as string | null,
        session_id: r.session_id as string | null,
        system_context_json: r.system_context_json as string | null,
        embedded_session_key: r.embedded_session_key as string | null,
        started_at: r.started_at as number,
        completed_at: r.completed_at as number | null,
        gmt_create: r.gmt_create as number,
        gmt_modified: r.gmt_modified as number | null,
      };
      // Only include output_json when requested (it can be very large)
      if (includeNodeOutput) {
        snapshot.output_json = r.output_json as string | null;
      }
      return snapshot;
    });

    return { flow_runs: flowRuns, node_executions: nodeExecutions };
  }

  /**
   * Context for audit logging — identifies which flow/event triggered this callback.
   */
  private sendCallback(
    config: HttpCallbackConfig,
    payload: HttpCallbackPayload,
    context: { flowId: string; workflowId: string; notifyEvent: string; nodeId?: string },
  ): Promise<HttpCallbackDispatchResult> {
    return this.sendCallbackImpl(config, payload, context);
  }

  /**
   * Send a single HTTP POST callback with HMAC-SHA256 signature.
   * Includes retry logic for 5xx and network errors.
   * Each attempt (including retries) is recorded to http_callback_logs.
   */
  private async sendCallbackImpl(
    config: HttpCallbackConfig,
    payload: HttpCallbackPayload,
    context: { flowId: string; workflowId: string; notifyEvent: string; nodeId?: string },
  ): Promise<HttpCallbackDispatchResult> {
    const body = JSON.stringify(payload);
    const maxAttempts = 1 + config.maxRetries; // 1 initial + retries
    const baseDelay = config.retryDelayMs;
    const maxBodyBytes = this.logMaxIoKb * 1024;
    const maxRespBytes = 4 * 1024;
    console.log(`[http-callback] sendCallback: config=${config.id} url=${config.url} event=${context.notifyEvent} flowId=${context.flowId} maxAttempts=${maxAttempts} signed=${!!config.secret}`);

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const startTime = Date.now();
      let responseCode: number | null = null;
      let responseBody = "";
      let errorMsg: string | null = null;
      let status: string = "sent";
      let requestHeaders: Record<string, string> = { "Content-Type": "application/json", "X-Callback-Config-Id": config.id };

      try {
        // Only sign when a secret is configured
        if (config.secret) {
          const timestamp = Date.now().toString();
          const signature = signPayload(config.secret, timestamp, body);
          requestHeaders["X-Callback-Timestamp"] = timestamp;
          requestHeaders["X-Callback-Signature-256"] = `sha256=${signature}`;
        }

        const response = await fetch(config.url, {
          method: "POST",
          headers: requestHeaders,
          body,
          signal: AbortSignal.timeout(config.timeoutMs),
        });

        responseCode = response.status;
        responseBody = await response.text().catch(() => "");
        const durationMs = Date.now() - startTime;
        const isDelivered = response.status >= 200 && response.status < 300;
        const isRetryable = response.status >= 500;

        if (!isDelivered && !isRetryable) {
          // 4xx (client error) — don't retry
          status = "skipped";
          errorMsg = `HTTP ${response.status}`;
          console.warn(`[http-callback] sendCallback: 4xx skipped config=${config.id} url=${config.url} status=${response.status} attempt=${attempt}`);
          this.insertAuditLog(config, context, body, requestHeaders, attempt, maxAttempts,
            responseCode, responseBody, durationMs, status, errorMsg, maxBodyBytes, maxRespBytes);
          return { sent: true, responseCode: response.status, error: `HTTP ${response.status}` };
        }

        if (isDelivered) {
          status = "delivered";
          console.log(`[http-callback] sendCallback: delivered config=${config.id} url=${config.url} httpStatus=${response.status} durationMs=${durationMs} attempt=${attempt}`);
          this.insertAuditLog(config, context, body, requestHeaders, attempt, maxAttempts,
            responseCode, responseBody, durationMs, status, null, maxBodyBytes, maxRespBytes);
          return { sent: true, responseCode: response.status, error: null };
        }

        // 5xx: retryable
        status = "failed";
        errorMsg = `HTTP ${response.status}`;
        console.warn(`[http-callback] sendCallback: 5xx retryable config=${config.id} url=${config.url} status=${response.status} attempt=${attempt}/${maxAttempts}`);
        this.insertAuditLog(config, context, body, requestHeaders, attempt, maxAttempts,
          responseCode, responseBody, durationMs, status, errorMsg, maxBodyBytes, maxRespBytes);

        if (attempt < config.maxRetries) {
          const delay = baseDelay * Math.pow(2, attempt);
          await sleep(delay);
        } else {
          console.warn(`[http-callback] sendCallback: exhausted retries config=${config.id} url=${config.url} lastStatus=${response.status}`);
          return { sent: true, responseCode: response.status, error: `HTTP ${response.status}` };
        }
      } catch (error) {
        errorMsg = error instanceof Error ? error.message : String(error);
        const durationMs = Date.now() - startTime;
        status = "failed";
        console.warn(`[http-callback] sendCallback: network error config=${config.id} url=${config.url} attempt=${attempt}/${maxAttempts} error=${errorMsg}`);

        this.insertAuditLog(config, context, body, requestHeaders, attempt, maxAttempts,
          responseCode, responseBody, durationMs, status, errorMsg, maxBodyBytes, maxRespBytes);

        const isLastAttempt = attempt === maxAttempts - 1;
        if (isLastAttempt) {
          console.warn(`[http-callback] sendCallback: exhausted retries (network) config=${config.id} url=${config.url}`);
          return { sent: false, responseCode: null, error: errorMsg };
        }

        // Network error: retry with exponential backoff
        const delay = baseDelay * Math.pow(2, attempt);
        await sleep(delay);
      }
    }

    // Should not reach here, but just in case
    return { sent: false, responseCode: null, error: "Unknown error" };
  }

  /** Truncate a string to maxBytes, appending a marker if truncated. */
  private truncateForLog(value: string, maxBytes: number): string {
    if (value.length <= maxBytes) return value;
    return value.slice(0, maxBytes) + "...[truncated]";
  }

  /** Insert an audit log row (best-effort, fire-and-forget). */
  private insertAuditLog(
    config: HttpCallbackConfig,
    context: { flowId: string; workflowId: string; notifyEvent: string; nodeId?: string },
    requestBody: string,
    requestHeaders: Record<string, string>,
    attempt: number,
    maxAttempts: number,
    responseStatusCode: number | null,
    responseBody: string,
    durationMs: number,
    status: string,
    errorMessage: string | null,
    maxBodyBytes: number,
    maxRespBytes: number,
  ): void {
    if (!this.logEnabled || !this.logRepo) {
      console.log(`[http-callback] insertAuditLog: skipped (logEnabled=${this.logEnabled} logRepo=${!!this.logRepo}) config=${config.id} event=${context.notifyEvent}`);
      return;
    }

    const logHeaders = { ...requestHeaders };
    // Redact signing secret — only keep timestamp and signature prefix
    delete (logHeaders as Record<string, unknown>)["X-Callback-Signature-256"];

    void this.logRepo.insert({
      flowId: context.flowId,
      workflowId: context.workflowId,
      configId: config.id,
      configName: config.name,
      callbackUrl: config.url,
      notifyEvent: context.notifyEvent,
      nodeId: context.nodeId ?? null,
      attempt,
      maxAttempts,
      requestBody: this.truncateForLog(requestBody, maxBodyBytes),
      requestHeaders: JSON.stringify(logHeaders),
      responseStatusCode,
      responseBody: this.truncateForLog(responseBody, maxRespBytes),
      durationMs,
      status,
      errorMessage,
    }).catch(() => {
      // Best-effort — never block callback flow on log failure
    });
  }
}

/** Sleep for a given number of milliseconds. */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Convert DB rows (http_callback_configs) to HttpCallbackConfig objects.
 * Used when loading configs from the database into the in-memory cache.
 */
export function rowToHttpCallbackConfig(row: {
  config_id: string;
  workflow_id: string;
  name: string;
  url: string;
  secret: string | null;
  enabled: number;
  notify_on: string;
  timeout_ms: number;
  max_retries: number;
  retry_delay_ms: number;
  include_node_output: number;
}): HttpCallbackConfig {
  let notifyOn: NotifyEvent[];
  try {
    const parsed = JSON.parse(row.notify_on) as unknown[];
    notifyOn = parsed.filter(
      (v): v is NotifyEvent => typeof v === "string" && VALID_NOTIFY_EVENTS.includes(v as NotifyEvent),
    );
  } catch {
    notifyOn = [];
  }

  return {
    id: row.config_id,
    workflowId: row.workflow_id,
    name: row.name,
    url: row.url,
    secret: row.secret || undefined,
    enabled: row.enabled !== 0,
    notifyOn,
    timeoutMs: row.timeout_ms,
    maxRetries: row.max_retries,
    retryDelayMs: row.retry_delay_ms,
    includeNodeOutput: row.include_node_output !== 0,
  };
}

/**
 * Merge YAML-declared callbacks with DB-backed callbacks.
 * DB configs override YAML configs with the same name.
 * Returns a Map keyed by workflowId.
 */
export function mergeCallbackConfigs(
  yamlConfigs: Map<string, HttpCallbackConfig[]>,
  dbConfigs: Map<string, HttpCallbackConfig[]>,
): Map<string, HttpCallbackConfig[]> {
  const result = new Map<string, HttpCallbackConfig[]>();

  // Start with all YAML configs
  for (const [workflowId, configs] of yamlConfigs) {
    result.set(workflowId, [...configs]);
  }

  // Apply DB overrides
  for (const [workflowId, dbWorkflowConfigs] of dbConfigs) {
    const existing = result.get(workflowId) ?? [];

    for (const dbConfig of dbWorkflowConfigs) {
      const yamlIndex = existing.findIndex((c) => c.name === dbConfig.name);
      if (yamlIndex >= 0) {
        // DB config overrides YAML config with the same name
        existing[yamlIndex] = dbConfig;
      } else {
        // New DB-only config
        existing.push(dbConfig);
      }
    }

    result.set(workflowId, existing);
  }

  return result;
}

/**
 * Convert a YAML-declared HttpCallbackNotification to an HttpCallbackConfig.
 * Used when loading configs from workflow YAML specs.
 */
export function yamlNotificationToConfig(
  notification: import("./http-callback-types.js").HttpCallbackNotification,
  workflowId: string,
): HttpCallbackConfig {
  return {
    id: `yaml:${workflowId}:${notification.name}`,
    workflowId,
    name: notification.name,
    url: notification.url,
    secret: notification.secret || undefined,
    enabled: notification.enabled ?? true,
    notifyOn: notification.notifyOn,
    timeoutMs: notification.timeoutMs ?? 5000,
    maxRetries: notification.maxRetries ?? 2,
    retryDelayMs: notification.retryDelayMs ?? 1000,
    includeNodeOutput: notification.includeNodeOutput ?? false,
  };
}