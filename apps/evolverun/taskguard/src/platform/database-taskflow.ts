/**
 * DatabaseTaskFlowAdapter — TaskFlow implementation for MCP Server mode.
 *
 * When running outside OpenClaw (Claude Code, Hermes, TeClaw), there's no
 * `api.runtime.taskFlow.bindSession()` available. This adapter implements
 * the same TaskFlowAdapter interface using the existing `flow_runs` table
 * via `FlowRunApiRepository` — the same repository that OpenClaw plugin mode
 * uses to persist workflow runs.
 *
 * Two modes:
 * 1. **API mode** (preferred): Delegates status transitions to clawweb's
 *    `/api/internal/runs` endpoints. Flow status, current phase, and
 *    completion state survive process restarts.
 * 2. **In-memory mode** (fallback): Uses a local Map — flows are lost on
 *    process restart. Activated when no `FlowRunApiRepository` is provided.
 *
 * Field mapping (TaskFlow → flow_runs):
 * - flowId       → flow_id
 * - goal         → workflow_title
 * - status       → status
 * - currentStep  → current_phase
 * - stateJson    → params_json (supplemented in-memory)
 * - controllerId → identity_key
 * - sessionKey   → origin_session_key
 * - waitJson     → in-memory only (MCP client re-sends on resume)
 * - revision     → in-memory counter (no optimistic lock in flow_runs)
 *
 * @module platform/database-taskflow
 */

import type { TaskFlowAdapter } from "./types.js";
import type { FlowRunApiRepository } from "../db/api-repositories/flow-run-api-repository.js";

// ── Types ──

interface InMemoryFlow {
  flowId: string;
  goal: string;
  status: string;
  currentStep: string;
  stateJson: string;
  revision: number;
  waitJson?: string;
  createdAt: string;
  updatedAt: string;
  controllerId: string;
  /** Scoping key — `tenantId:sessionKey` when tenantId is provided, otherwise plain `sessionKey`. */
  sessionScope: string;
}

/** Constructor options for DatabaseTaskFlowAdapter. */
export interface DatabaseTaskFlowAdapterOptions {
  /** API-backed repository for flow_runs persistence. When provided, API mode is used. */
  flowRunApiRepo?: FlowRunApiRepository;
  /** Session key for scoping flow state queries. */
  sessionKey: string;
  /** Tenant ID for multi-tenant session isolation. When provided, the storage key becomes `tenantId:sessionKey`. */
  tenantId?: string;
}

// ── Helpers ──

let flowCounter = 0;

/** Random hex digit for UUID generation */
function hexDigit(): string {
  return Math.floor(Math.random() * 16).toString(16);
}

/**
 * Generate flow_id in UUID v4 format, matching OpenClaw's TaskFlow convention.
 * OpenClaw uses standard UUID (e.g. "9c178113-c3fb-4e41-89bf-1314a5e0d176"),
 * so standalone MCP mode must produce the same format for consistency
 * with clawweb UI and cross-mode flow lookups.
 */
function generateFlowId(): string {
  flowCounter++;
  // UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
  // where y is one of 8,9,a,b
  const y = (8 + Math.floor(Math.random() * 4)).toString(16);
  return [
    Array.from({ length: 8 }, () => hexDigit()).join(""),
    Array.from({ length: 4 }, () => hexDigit()).join(""),
    "4" + Array.from({ length: 3 }, () => hexDigit()).join(""),
    y + Array.from({ length: 3 }, () => hexDigit()).join(""),
    Array.from({ length: 12 }, () => hexDigit()).join(""),
  ].join("-");
}

function now(): string {
  return new Date().toISOString();
}

/**
 * Normalize the result of `list()` — TaskFlow returns either
 * `{ flows: [...] }` or `Flow[]` directly.
 */
function normalizeFlowListResult(
  result: { flows: Array<Record<string, unknown>> } | Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  if (Array.isArray(result)) return result;
  if (result && "flows" in result && Array.isArray(result.flows)) return result.flows;
  return [];
}

/** Convert a FlowRunRow from the API into the Record shape the Controller expects. */
function rowToFlowRecord(row: Record<string, unknown>): Record<string, unknown> {
  // Extract stateJson from params_json (may contain embedded _clawmind_state)
  let stateJson = "{}";
  const paramsJson = row.params_json;
  if (typeof paramsJson === "string" && paramsJson) {
    try {
      const parsed = JSON.parse(paramsJson);
      if (parsed._clawmind_state) {
        stateJson = typeof parsed._clawmind_state === "string"
          ? parsed._clawmind_state
          : JSON.stringify(parsed._clawmind_state);
      } else {
        stateJson = paramsJson;
      }
    } catch {
      stateJson = paramsJson;
    }
  }

  return {
    flowId: row.flow_id,
    goal: row.workflow_title ?? row.workflow_id ?? "",
    status: row.status,
    currentStep: row.current_phase ?? "start",
    stateJson,
    revision: 1, // flow_runs has no revision — always report 1
    waitJson: undefined, // waitJson is not persisted in flow_runs
    createdAt: typeof row.gmt_create === "number"
      ? new Date(row.gmt_create * 1000).toISOString()
      : String(row.gmt_create ?? now()),
    updatedAt: typeof row.gmt_modified === "number"
      ? new Date(row.gmt_modified * 1000).toISOString()
      : String(row.gmt_modified ?? now()),
    controllerId: row.identity_key ?? row.triggered_by ?? "mcp-server",
    sessionKey: row.origin_session_key ?? "",
  };
}

/** Convert an InMemoryFlow to the Record shape the Controller expects. */
function inMemoryToFlowRecord(flow: InMemoryFlow): Record<string, unknown> {
  return { ...flow, sessionKey: flow.sessionScope };
}

/** Rebuild the supplementary in-memory state after an API-backed recovery. */
function recoveredRecordToInMemoryFlow(
  record: Record<string, unknown>,
  fallbackFlowId: string,
): InMemoryFlow {
  const revision = Number(record.revision ?? 1);
  return {
    flowId: String(record.flowId ?? fallbackFlowId),
    goal: String(record.goal ?? ""),
    status: String(record.status ?? "running"),
    currentStep: String(record.currentStep ?? "start"),
    stateJson: String(record.stateJson ?? "{}"),
    revision: Number.isFinite(revision) && revision > 0 ? revision : 1,
    waitJson: typeof record.waitJson === "string" ? record.waitJson : undefined,
    createdAt: String(record.createdAt ?? now()),
    updatedAt: String(record.updatedAt ?? now()),
    controllerId: String(record.controllerId ?? "mcp-server"),
    sessionScope: String(record.sessionKey ?? ""),
  };
}

// ── DatabaseTaskFlowAdapter ──

/**
 * TaskFlow adapter for non-OpenClaw platforms.
 *
 * Supports two persistence modes:
 * - **API mode**: When `flowRunApiRepo` is provided, status transitions
 *   go through clawweb's `/api/internal/runs` endpoints. The flow_runs
 *   table serves as the source of truth for status/current_phase/completion.
 *   Supplementary data (waitJson, stateJson, revision) is kept in-memory.
 * - **In-memory mode**: When no `flowRunApiRepo`, uses a per-session Map.
 *   Flows exist only for the lifetime of the process.
 */
export class DatabaseTaskFlowAdapter implements TaskFlowAdapter {
  /** Shared in-memory flow store — all instances share this so that adapters with the same storageKey can see each other's flows. */
  private static flows = new Map<string, InMemoryFlow>();

  /** Reset the shared flow store. Intended for use in tests only. */
  static _resetForTesting(): void {
    DatabaseTaskFlowAdapter.flows.clear();
  }

  /** Explicit unscoped read for privileged process-internal queries. */
  static getGlobalFlow(flowId: string): Record<string, unknown> | null {
    const flow = DatabaseTaskFlowAdapter.flows.get(flowId);
    return flow ? inMemoryToFlowRecord(flow) : null;
  }

  /** Explicit unscoped list for privileged process-internal queries. */
  static listGlobalFlows(): Array<Record<string, unknown>> {
    return Array.from(DatabaseTaskFlowAdapter.flows.values(), inMemoryToFlowRecord);
  }

  private readonly apiRepo?: FlowRunApiRepository;
  private readonly sessionKey: string;
  private readonly storageKey: string;

  constructor(sessionKey: string, flowRunApiRepo?: FlowRunApiRepository);
  constructor(options: DatabaseTaskFlowAdapterOptions);
  constructor(sessionKeyOrOptions: string | DatabaseTaskFlowAdapterOptions, flowRunApiRepo?: FlowRunApiRepository) {
    if (typeof sessionKeyOrOptions === "string") {
      this.sessionKey = sessionKeyOrOptions;
      this.apiRepo = flowRunApiRepo;
      this.storageKey = sessionKeyOrOptions;
    } else {
      this.sessionKey = sessionKeyOrOptions.sessionKey;
      this.apiRepo = sessionKeyOrOptions.flowRunApiRepo;
      this.storageKey = sessionKeyOrOptions.tenantId
        ? `${sessionKeyOrOptions.tenantId}:${sessionKeyOrOptions.sessionKey}`
        : sessionKeyOrOptions.sessionKey;
    }
  }

  private getBoundFlow(flowId: string): InMemoryFlow | undefined {
    const flow = DatabaseTaskFlowAdapter.flows.get(flowId);
    return flow?.sessionScope === this.storageKey ? flow : undefined;
  }

  // ── TaskFlowAdapter methods ──

  async createManaged(params: Record<string, unknown>): Promise<Record<string, unknown>> {
    const flowId = generateFlowId();
    const goal = String(params.goal ?? "");
    const status = String(params.status ?? "running");
    const currentStep = String(params.currentStep ?? "start");
    const stateJson = String(params.stateJson ?? "{}");
    const waitJson = params.waitJson ? String(params.waitJson) : null;
    const controllerId = String(params.controllerId ?? "mcp-server");
    const workflowId = String(params.workflowId ?? "unknown-workflow");

    // Always store in-memory for supplementary data (waitJson, stateJson, revision)
    const flow: InMemoryFlow = {
      flowId,
      goal,
      status,
      currentStep,
      stateJson,
      revision: 1,
      waitJson: waitJson ?? undefined,
      createdAt: now(),
      updatedAt: now(),
      controllerId,
      sessionScope: this.storageKey,
    };
    DatabaseTaskFlowAdapter.flows.set(flowId, flow);

    // Also persist via API if available
    if (this.apiRepo) {
      try {
        // Embed stateJson into params_json so it survives restarts
        const paramsJson = JSON.stringify({ _clawmind_state: stateJson });
        const ok = await this.apiRepo.insert({
          flowId,
          workflowId,
          workflowTitle: goal,
          status,
          paramsJson,
          startedAt: Math.floor(Date.now() / 1000),
          triggeredBy: controllerId,
          identityKey: controllerId,
          originSessionKey: this.storageKey,
        });
        if (!ok) {
          console.warn(`[clawmind:taskflow] API insert failed for ${flowId}, status persists in-memory only`);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawmind:taskflow] API insert threw for ${flowId}: ${msg}, status persists in-memory only`);
      }
    }

    return inMemoryToFlowRecord(flow);
  }

  async setWaiting(params: Record<string, unknown>): Promise<{ applied: boolean; flow: Record<string, unknown> }> {
    const flowId = String(params.flowId);
    const currentStep = String(params.currentStep ?? "");
    const stateJson = String(params.stateJson ?? "{}");
    const waitJson = params.waitJson ? String(params.waitJson) : null;
    const expectedRevision = Number(params.expectedRevision ?? 0);

    // Update in-memory
    const flow = this.getBoundFlow(flowId);
    if (!flow) {
      return { applied: false, flow: { flowId, status: "not_found" } };
    }
    const rev = expectedRevision || flow.revision;
    if (rev !== flow.revision) {
      throw new Error(`状态更新冲突，请重试 (expected ${rev}, current ${flow.revision})`);
    }
    flow.status = "waiting";
    flow.currentStep = currentStep || flow.currentStep;
    flow.stateJson = stateJson === "{}" ? flow.stateJson : stateJson;
    flow.waitJson = waitJson ?? undefined;
    flow.revision++;
    flow.updatedAt = now();

    // Persist status transition via API
    if (this.apiRepo) {
      try {
        await this.apiRepo.updateStatus(flowId, "waiting");
        if (currentStep) {
          await this.apiRepo.updateCurrentPhase(flowId, currentStep);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawmind:taskflow] API setWaiting threw for ${flowId}: ${msg}`);
      }
    }

    return { applied: true, flow: inMemoryToFlowRecord(flow) };
  }

  async resume(params: Record<string, unknown>): Promise<{ applied: boolean; flow: Record<string, unknown> }> {
    const flowId = String(params.flowId);
    const status = String(params.status ?? "running");
    const currentStep = String(params.currentStep ?? "");
    const stateJson = String(params.stateJson ?? "{}");
    const expectedRevision = Number(params.expectedRevision ?? 0);

    const flow = this.getBoundFlow(flowId);
    if (!flow) {
      return { applied: false, flow: { flowId, status: "not_found" } };
    }
    const rev = expectedRevision || flow.revision;
    if (rev !== flow.revision) {
      throw new Error(`状态更新冲突，请重试 (expected ${rev}, current ${flow.revision})`);
    }
    flow.status = status;
    flow.currentStep = currentStep || flow.currentStep;
    flow.stateJson = stateJson === "{}" ? flow.stateJson : stateJson;
    flow.waitJson = undefined;
    flow.revision++;
    flow.updatedAt = now();

    if (this.apiRepo) {
      try {
        await this.apiRepo.updateStatus(flowId, status);
        if (currentStep) {
          await this.apiRepo.updateCurrentPhase(flowId, currentStep);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawmind:taskflow] API resume threw for ${flowId}: ${msg}`);
      }
    }

    return { applied: true, flow: inMemoryToFlowRecord(flow) };
  }

  async finish(params: Record<string, unknown>): Promise<unknown> {
    const flowId = String(params.flowId);
    const stateJson = String(params.stateJson ?? "{}");
    const expectedRevision = Number(params.expectedRevision ?? 0);

    const flow = this.getBoundFlow(flowId);
    if (!flow) return { applied: false, flowId, status: "not_found" };
    const rev = expectedRevision || flow.revision;
    if (rev !== flow.revision) {
      throw new Error(`状态更新冲突，请重试 (expected ${rev}, current ${flow.revision})`);
    }
    // NOTE: "completed" is the legacy TaskFlow status value. We map it to
    // "succeeded" here so that flow_runs.status uses the unified status set.
    flow.status = "succeeded";
    flow.stateJson = stateJson === "{}" ? flow.stateJson : stateJson;
    flow.revision++;
    flow.updatedAt = now();

    // FUNDAMENTAL FIX: Do NOT call apiRepo.updateCompletion() here.
    //
    // Previously, finish() fired an HTTP PUT to /runs/{id}/completion with
    // resultJson=undefined, and completeFlowRun() (called immediately after)
    // fired another HTTP PUT with the structured resultJson. These two async
    // PUTs to the same endpoint could race — if finish()'s PUT arrived after
    // completeFlowRun()'s, it would overwrite the structured result_json.
    //
    // Now finish() only updates the in-memory flow state. The DB write is
    // handled exclusively by completeFlowRun(), which is the SOLE writer of
    // flow_runs.status and flow_runs.result_json at flow completion.

    return { ...inMemoryToFlowRecord(flow), applied: true };
  }

  async fail(params: Record<string, unknown>): Promise<unknown> {
    const flowId = String(params.flowId);
    const status = String(params.status ?? "failed");
    const stateJson = String(params.stateJson ?? "{}");
    const expectedRevision = Number(params.expectedRevision ?? 0);

    const flow = this.getBoundFlow(flowId);
    if (!flow) return { applied: false, flowId, status: "not_found" };
    const rev = expectedRevision || flow.revision;
    if (rev !== flow.revision) {
      throw new Error(`状态更新冲突，请重试 (expected ${rev}, current ${flow.revision})`);
    }
    flow.status = status;
    flow.stateJson = stateJson === "{}" ? flow.stateJson : stateJson;
    flow.revision++;
    flow.updatedAt = now();

    if (this.apiRepo) {
      try {
        await this.apiRepo.updateStatus(flowId, status);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawmind:taskflow] API fail threw for ${flowId}: ${msg}`);
      }
    }

    return { ...inMemoryToFlowRecord(flow), applied: true };
  }

  async list(): Promise<{ flows: Array<Record<string, unknown>> } | Array<Record<string, unknown>>> {
    // list by session_key is not supported by the existing /runs API,
    // so we always use in-memory. The API is used primarily for individual
    // flow status transitions (createManaged, setWaiting, finish, etc.)
    // and get-by-flowId recovery.
    // Filter by sessionScope so each adapter instance only sees its own flows.
    const flows = Array.from(DatabaseTaskFlowAdapter.flows.values())
      .filter(f => f.sessionScope === this.storageKey)
      .map(inMemoryToFlowRecord);
    return { flows };
  }

  async get(token: string): Promise<Record<string, unknown> | null> {
    // Try in-memory first (has full data including waitJson and stateJson)
    const flow = DatabaseTaskFlowAdapter.flows.get(token);
    if (flow) {
      return flow.sessionScope === this.storageKey ? inMemoryToFlowRecord(flow) : null;
    }

    // Fallback to API (useful for recovery after process restart)
    if (this.apiRepo) {
      try {
        const row = await this.apiRepo.findByFlowId(token);
        const current = DatabaseTaskFlowAdapter.flows.get(token);
        if (current) {
          return current.sessionScope === this.storageKey ? inMemoryToFlowRecord(current) : null;
        }
        if (row) {
          const record = rowToFlowRecord(row as Record<string, unknown>);
          const recovered = recoveredRecordToInMemoryFlow(record, token);
          const currentByRecoveredId = DatabaseTaskFlowAdapter.flows.get(recovered.flowId);
          if (currentByRecoveredId) {
            return currentByRecoveredId.sessionScope === this.storageKey
              ? inMemoryToFlowRecord(currentByRecoveredId)
              : null;
          }
          if (recovered.sessionScope !== this.storageKey) return null;
          DatabaseTaskFlowAdapter.flows.set(recovered.flowId, recovered);
          return inMemoryToFlowRecord(recovered);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[clawmind:taskflow] API get threw for ${token}: ${msg}`);
      }
    }

    return null;
  }

  async findLatest(): Promise<Record<string, unknown> | null> {
    // findLatestBySessionKey is not supported by the existing /runs API.
    // Use in-memory only. Filter by sessionScope for multi-tenant isolation.
    let latest: InMemoryFlow | null = null;
    for (const flow of DatabaseTaskFlowAdapter.flows.values()) {
      if (flow.sessionScope !== this.storageKey) continue;
      if (!latest || flow.updatedAt >= latest.updatedAt) {
        latest = flow;
      }
    }
    return latest ? inMemoryToFlowRecord(latest) : null;
  }

  async runTask(params: Record<string, unknown>): Promise<Record<string, unknown>> {
    // runTask is used for sub-task execution within a flow.
    // Always in-memory — sub-tasks are short-lived and don't need persistence.
    const flowId = String(params.flowId ?? "");
    const flow = this.getBoundFlow(flowId);
    if (!flow) {
      return { status: "not_found", flowId };
    }
    return {
      status: "succeeded",
      flowId,
      result: params,
    };
  }
}
