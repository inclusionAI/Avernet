import { createHash, randomUUID } from "node:crypto";
import type { FlowState, WorkflowNode, WorkflowSpec } from "../types.js";
import { MAX_SUBWORKFLOW_DEPTH } from "../types.js";
import { resolveTemplate, tryResolveSingleTemplate } from "../runner.js";
import type { TemplateContext } from "../runner.js";
import type {
  FanoutConfig, FanoutState, FanoutChildState, FanoutChildRequest, FanoutError,
  FanoutCounts, FanoutResult, FanoutChildCompletion,
} from "./subworkflow-fanout-types.js";

export const errorDetail = (code: string, message: string, category: FanoutError["category"] = "contract"): FanoutError => ({ code, message, category });
export const terminalChild = (c: FanoutChildState): boolean =>
  ["succeeded", "failed", "cancelled", "timed_out", "rejected"].includes(c.status);
export const terminalGroup = (s: FanoutState): boolean =>
  ["succeeded", "failed", "cancelled", "timed_out"].includes(s.status);
export const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));

/** Reject lossy values/cycles instead of silently changing the durable contract. */
export function snapshot<T>(value: T): T {
  const seen = new Set<object>();
  function visit(v: unknown): void {
    if (v === null || typeof v === "string" || typeof v === "boolean") return;
    if (typeof v === "number" && Number.isFinite(v)) return;
    if (!Array.isArray(v) && !record(v)) throw new Error("fanout values must be JSON serializable");
    if (seen.has(v)) throw new Error("fanout values must not contain cycles");
    seen.add(v);
    for (const entry of Array.isArray(v) ? v : Object.values(v)) {
      if (entry === undefined && !Array.isArray(v)) continue; // optional spec fields
      visit(entry);
    }
    seen.delete(v);
  }
  visit(value);
  return JSON.parse(JSON.stringify(value)) as T;
}

function validateConfig(value: unknown): asserts value is FanoutConfig {
  if (!record(value)) throw new Error("fanout configuration is required");
  const keys = ["schemaVersion", "items", "itemIdFrom", "concurrency", "successPolicy", "finishMode", "resultPolicy", "groupTimeoutSeconds", "cancelGraceSeconds"];
  if (Object.keys(value).some(k => !keys.includes(k))) throw new Error("unsupported fanout configuration field");
  if (value.schemaVersion !== "fanout.v1" || value.successPolicy !== "all_done"
    || value.finishMode !== "wait_all" || value.resultPolicy !== "collect_all") throw new Error("unsupported fanout policy");
  if (!Number.isSafeInteger(value.concurrency) || (value.concurrency as number) < 1) throw new Error("concurrency must be a positive integer");
  for (const key of ["groupTimeoutSeconds", "cancelGraceSeconds"] as const) {
    if (typeof value[key] !== "number" || !Number.isFinite(value[key]) || value[key] <= 0
      || value[key] * 1000 > Number.MAX_SAFE_INTEGER - Date.now()) throw new Error(`${key} must be a finite valid duration`);
  }
  if (!Array.isArray(value.itemIdFrom) || !value.itemIdFrom.length
    || value.itemIdFrom.some(k => typeof k !== "string" || !k.trim())
    || new Set(value.itemIdFrom).size !== value.itemIdFrom.length) throw new Error("itemIdFrom must contain unique nonempty field names");
}

export function createState(
  node: WorkflowNode, ctx: TemplateContext, parent: FlowState,
  parentFlowId: string, childWorkflow: WorkflowSpec,
): FanoutState {
  if (node.executor.type !== "subworkflow") throw new Error("fanout requires a subworkflow executor");
  const executor = node.executor as typeof node.executor & { fanout?: FanoutConfig };
  const config = executor.fanout;
  validateConfig(config);
  if (executor.onFailure && executor.onFailure !== "fail") throw new Error("fanout does not support skip or automatic retry");
  if (!childWorkflow || childWorkflow.id !== executor.workflowId) throw new Error("resolved child snapshot does not match workflowId");
  if (!Object.hasOwn(childWorkflow.outputs ?? {}, "result")
    || typeof childWorkflow.outputs.result.from !== "string" || !childWorkflow.outputs.result.from.trim()) {
    throw new Error("CHILD_EXPORT_INVALID: child workflow must explicitly declare outputs.result.from");
  }
  const childPublishedVersion = parent.subworkflowDependencies?.[executor.workflowId]?.publishedVersion;
  if (!Number.isSafeInteger(childPublishedVersion) || childPublishedVersion <= 0) {
    throw new Error("DEPENDENCY_LOCK_INVALID: parent must provide the pinned child's publishedVersion");
  }
  const depth = (parent.subworkflowMeta?.depth ?? 0) + 1;
  if (depth > MAX_SUBWORKFLOW_DEPTH) throw new Error("maximum subworkflow nesting depth exceeded");
  // Never stringify+parse a template result: a string containing JSON is not an array.
  const raw = typeof config.items === "string" ? tryResolveSingleTemplate(config.items, ctx)?.value : config.items;
  if (!Array.isArray(raw)) throw new Error("items must resolve once to a structured array");
  const items = snapshot(raw);
  const groupExecutionId = randomUUID();
  const ids = new Set<string>();
  const children: FanoutChildState[] = items.map((item, itemIndex) => {
    if (!record(item)) throw new Error("each fanout item must be an object");
    const itemId = config.itemIdFrom.map(key => {
      const v = Object.hasOwn(item, key) ? item[key] : undefined;
      if (typeof v !== "string" || !v.trim()) throw new Error(`itemIdFrom field ${key} must be a nonempty string`);
      return encodeURIComponent(v).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
    }).join("/");
    if (ids.has(itemId)) throw new Error(`duplicate composite itemId: ${itemId}`);
    ids.add(itemId);
    const itemContext = { params: parent.params, ...ctx, item, itemIndex };
    const entries = executor.params && Object.keys(executor.params).length ? Object.entries(executor.params) : null;
    const params = entries ? Object.fromEntries(entries.map(([key, value]) => {
      if (typeof value !== "string") throw new Error(`param ${key} must be a template string`);
      const single = tryResolveSingleTemplate(value, itemContext);
      const bound = single ? single.value : resolveTemplate(value, itemContext);
      if (bound === undefined) throw new Error(`param ${key} template is unresolved`);
      return [key, bound];
    })) : parent.params;
    const childFlowId = randomUUID();
    return { parentFlowId, parentNodeId: node.id, groupExecutionId, itemId, itemIndex,
      attemptNumber: 1, childFlowId, sessionKey: `fanout:${childFlowId}`,
      stateDirRef: `fanout-state:${childFlowId}`, depth, params: snapshot(params), item,
      status: "queued", result: null, error: null };
  });
  const createdAt = new Date().toISOString();
  return {
    schemaVersion: "fanout.v1", kind: "subworkflowFanoutState", parentFlowId, groupNodeId: node.id,
    groupExecutionId, revision: 0, status: "running", createdAt,
    deadlineAt: new Date(Date.parse(createdAt) + config.groupTimeoutSeconds * 1000).toISOString(),
    sealedAt: null, cancelRequestedAt: null, cancelDeadlineAt: null,
    config: snapshot(config), childWorkflow: snapshot(childWorkflow), childPublishedVersion, children,
    inputDigest: `sha256:${createHash("sha256").update(JSON.stringify(children.map(c => ({ item: c.item, params: c.params })))).digest("hex")}`,
    error: null, cleanupPendingChildIds: [], result: null,
  };
}

export function childRequest(state: FanoutState, child: FanoutChildState): FanoutChildRequest {
  const { item: _item, status: _status, result: _result, error: _error, ...identity } = child;
  return snapshot({ ...identity, childWorkflow: state.childWorkflow });
}

export function projectResult(state: FanoutState): FanoutResult {
  const counts: FanoutCounts = { total: state.children.length, successCount: 0, failedCount: 0,
    cancelledCount: 0, timedOutCount: 0, rejectedCount: 0, pendingCount: 0 };
  for (const c of state.children) {
    if (c.status === "succeeded") counts.successCount++;
    else if (c.status === "failed") counts.failedCount++;
    else if (c.status === "cancelled") counts.cancelledCount++;
    else if (c.status === "timed_out") counts.timedOutCount++;
    else if (c.status === "rejected") counts.rejectedCount++;
    else counts.pendingCount++;
  }
  const complete = counts.pendingCount === 0;
  const businessOutcome = !complete || counts.cancelledCount || counts.timedOutCount || counts.rejectedCount
    ? "incomplete" : counts.total === 0 ? "empty" : counts.failedCount === 0 ? "all_success"
      : counts.successCount ? "partial_failure" : "all_failed";
  return snapshot({ schemaVersion: "fanout.v1", kind: "subworkflowFanoutResult",
    parentFlowId: state.parentFlowId, groupNodeId: state.groupNodeId, groupExecutionId: state.groupExecutionId,
    groupRevision: state.revision, childWorkflowId: state.childWorkflow.id, childPublishedVersion: state.childPublishedVersion,
    status: state.status, businessOutcome, complete, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
    ...counts, counts, error: state.error,
    items: state.children.map(c => ({ itemIndex: c.itemIndex, itemId: c.itemId, item: c.item,
      attemptNumber: c.attemptNumber, childFlowId: c.childFlowId, status: c.status, result: c.result, error: c.error })),
  });
}

/** Normalize untrusted port data without treating infrastructure errors as business failures. */
export function applyCompletion(child: FanoutChildState, completion: FanoutChildCompletion): void {
  child.result = null;
  if (completion?.status === "succeeded") {
    try {
      if (!record(completion.outputs) || !Object.hasOwn(completion.outputs, "result") || !record(completion.outputs.result)) throw new Error("missing or invalid result");
      child.result = snapshot(completion.outputs.result);
      child.status = "succeeded"; child.error = null;
      return;
    } catch {
      child.status = "failed";
      child.error = errorDetail("CHILD_EXPORT_INVALID", "Successful child must explicitly export a JSON object at outputs.result");
      return;
    }
  }
  const statuses = ["failed", "cancelled", "timed_out", "rejected"];
  if (!completion || !statuses.includes(completion.status)) throw new Error("invalid child completion status");
  child.status = completion.status;
  const detail = completion.error;
  if (detail && (!record(detail) || typeof detail.code !== "string" || !detail.code
    || typeof detail.message !== "string" || !["business", "execution", "contract", "orchestration", "authorization", "persistence", "timeout", "cancellation"].includes(detail.category))) {
    throw new Error("invalid structured child error");
  }
  child.error = detail ? snapshot(detail) : errorDetail(
    completion.status === "failed" ? "CHILD_ERROR_MISSING" : `CHILD_${completion.status.toUpperCase()}`,
    `Child terminated with ${completion.status}`,
    completion.status === "timed_out" ? "timeout" : completion.status === "cancelled" ? "cancellation" : "orchestration",
  );
}

export function fatalChild(child: FanoutChildState): boolean {
  return terminalChild(child) && child.status !== "succeeded"
    && !(child.status === "failed" && ["business", "execution"].includes(child.error?.category));
}
