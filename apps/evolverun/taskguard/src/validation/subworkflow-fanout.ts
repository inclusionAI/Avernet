import type { FanoutConfig } from "../executors/subworkflow-fanout-types.js";
import type { WorkflowSpec } from "../types.js";
import { canonicalWorkflowJson, collectSubworkflowExecutors } from "../workflow-dependencies.js";

type Fail = (path: string, message: string) => never;
const keys = new Set([
  "schemaVersion", "items", "itemIdFrom", "concurrency", "successPolicy", "finishMode",
  "resultPolicy", "groupTimeoutSeconds", "cancelGraceSeconds",
]);
// Same path grammar as tryResolveSingleTemplate; v1 permits a reference, not a fallback/expression.
const singleReference = /^\s*\{\{[\w.@-]+(?:\.[\w.@-]+|\[\d+\])*\}\}\s*$/;

/** Reject before deep-preserve: stripping unknown keys would put them straight back. */
export function normalizeSubworkflowFanout(
  raw: unknown, version: unknown, path: string, fail: Fail, onFailure?: unknown,
): FanoutConfig | undefined {
  if (version !== undefined && version !== "pinned") fail(`${path}.version`, 'must be "pinned"');
  if (raw === undefined) return undefined;
  if (version !== "pinned") fail(`${path}.version`, 'fanout requires version: "pinned"');
  if (onFailure !== undefined && onFailure !== "fail") fail(`${path}.onFailure`, "fanout.v1 does not support skip or automatic retry");
  const base = `${path}.fanout`;
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) fail(base, "must be an object");
  const obj = raw as Record<string, unknown>;
  for (const key of Object.keys(obj)) {
    if (!keys.has(key)) fail(`${base}.${key}`, "unknown fanout.v1 field");
  }
  for (const [key, value] of Object.entries({
    schemaVersion: "fanout.v1", successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
  })) {
    if (obj[key] !== value) fail(`${base}.${key}`, `must be "${value}"`);
  }
  const staticItems = Array.isArray(obj.items) && obj.items.every(item =>
    item !== null && typeof item === "object" && !Array.isArray(item)
    && [Object.prototype, null].includes(Object.getPrototypeOf(item)));
  if (!staticItems && (typeof obj.items !== "string" || !singleReference.test(obj.items))) {
    fail(`${base}.items`, "must be a single template reference or an array of objects");
  }
  if (!Array.isArray(obj.itemIdFrom) || obj.itemIdFrom.length === 0
    || obj.itemIdFrom.some(key => typeof key !== "string" || !key.trim())
    || new Set(obj.itemIdFrom.map(key => typeof key === "string" ? key.trim() : key)).size !== obj.itemIdFrom.length) {
    fail(`${base}.itemIdFrom`, "must be a non-empty array of unique non-empty strings");
  }
  if (!Number.isSafeInteger(obj.concurrency) || (obj.concurrency as number) <= 0) {
    fail(`${base}.concurrency`, "must be a positive safe integer");
  }
  for (const key of ["groupTimeoutSeconds", "cancelGraceSeconds"]) {
    if (typeof obj[key] !== "number" || !Number.isFinite(obj[key]) || obj[key] <= 0) {
      fail(`${base}.${key}`, "must be a finite positive number of seconds");
    }
  }
  if (Array.isArray(obj.items)) {
    try { canonicalWorkflowJson(obj.items); }
    catch { fail(`${base}.items`, "static items must contain only JSON values"); }
    const identities = new Set<string>();
    obj.items.forEach((item, index) => {
      const fields = (obj.itemIdFrom as string[]).map(key => {
        const value = Object.hasOwn(item, key) ? item[key] : undefined;
        if (typeof value !== "string" || !value.trim()) fail(`${base}.items[${index}].${key}`, "item identity field must be a non-empty string");
        return value;
      });
      const identity = JSON.stringify(fields);
      if (identities.has(identity)) fail(`${base}.items[${index}]`, "duplicate composite item identity");
      identities.add(identity);
    });
  }
  return { ...obj, itemIdFrom: [...obj.itemIdFrom as string[]] } as FanoutConfig;
}

export function validateSubworkflowFanouts(workflow: WorkflowSpec, fail: Fail): void {
  for (const { executor, path } of collectSubworkflowExecutors(workflow)) {
    normalizeSubworkflowFanout(executor.fanout, executor.version, path, fail, executor.onFailure);
  }
}
