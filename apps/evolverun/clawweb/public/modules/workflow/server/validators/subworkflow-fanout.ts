/** Validate the ClawMind fanout.v1 shape without rewriting the submitted executor. */
const fanoutKeys = new Set([
  "schemaVersion", "items", "itemIdFrom", "concurrency", "successPolicy", "finishMode",
  "resultPolicy", "groupTimeoutSeconds", "cancelGraceSeconds",
]);
const singleReference = /^\s*\{\{([\w.@-]+(?:\.[\w.@-]+|\[\d+\])*)(?:\s*\|\s*default:\s*([^}]+))?\}\}\s*$/;
const executorKeys = new Set(["type", "workflowId", "packId", "version", "fanout", "params", "onFailure"]);

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

// Match the engine schema: JSON values only, with no sparse arrays or cycles.
function isJson(value: unknown, active = new Set<object>()): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || (!Array.isArray(value) && !record(value)) || active.has(value)) return false;
  active.add(value);
  let valid: boolean;
  if (Array.isArray(value)) {
    valid = true;
    for (let i = 0; i < value.length; i++) {
      if (!Object.hasOwn(value, i) || !isJson(value[i], active)) { valid = false; break; }
    }
  } else {
    valid = Object.keys(value).every(key => isJson(value[key], active));
  }
  active.delete(value);
  return valid;
}

function validateFanout(executor: Record<string, unknown>, path: string): void {
  if (!Object.hasOwn(executor, "fanout")) return;
  const base = `${path}.fanout`;
  const fail = (field: string, message: string): never => { throw new Error(`Validation error: ${field} ${message}`); };
  for (const key of Object.keys(executor)) {
    if (!executorKeys.has(key)) fail(`${path}.${key}`, "unknown field");
  }
  for (const key of ["workflowId", "packId"]) {
    if (key === "packId" && executor[key] === undefined) continue;
    if (typeof executor[key] !== "string" || !(executor[key] as string).trim()) {
      fail(`${path}.${key}`, "must be a non-empty string");
    }
  }
  const version = executor.version;
  if (version !== undefined && version !== "inherit"
    && (typeof version !== "number" || !Number.isSafeInteger(version) || version < 1)) {
    fail(`${path}.version`, 'must be a positive integer or "inherit"');
  }
  if (executor.params !== undefined) {
    if (!record(executor.params)) fail(`${path}.params`, "must be a plain object");
    for (const [key, value] of Object.entries(executor.params as Record<string, unknown>)) {
      if (typeof value !== "string") fail(`${path}.params.${key}`, "must be a string");
    }
  }
  if (executor.onFailure !== undefined && executor.onFailure !== "fail") {
    fail(`${path}.onFailure`, "fanout.v1 does not support skip or automatic retry");
  }
  if (!record(executor.fanout)) fail(base, "must be an object");
  const fanout = executor.fanout as Record<string, unknown>;
  for (const key of Object.keys(fanout)) {
    if (!fanoutKeys.has(key)) fail(`${base}.${key}`, "unknown fanout.v1 field");
  }
  for (const [key, expected] of Object.entries({
    schemaVersion: "fanout.v1", successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
  })) {
    if (fanout[key] !== expected) fail(`${base}.${key}`, `must be "${expected}"`);
  }
  const items = fanout.items;
  if (!(Array.isArray(items) && items.every(record))
    && (typeof items !== "string" || !singleReference.test(items))) {
    fail(`${base}.items`, "must be a single template reference or an array of objects");
  }
  const ids = fanout.itemIdFrom;
  if (!Array.isArray(ids) || ids.length === 0 || ids.some(key => typeof key !== "string" || !key.trim())
    || new Set(ids).size !== ids.length) {
    fail(`${base}.itemIdFrom`, "must be a non-empty array of unique non-empty strings");
  }
  if (!Number.isSafeInteger(fanout.concurrency) || (fanout.concurrency as number) <= 0) {
    fail(`${base}.concurrency`, "must be a positive safe integer");
  }
  for (const key of ["groupTimeoutSeconds", "cancelGraceSeconds"]) {
    const duration = fanout[key];
    if (key === "cancelGraceSeconds" && duration === undefined) continue;
    if (typeof duration !== "number" || !Number.isFinite(duration) || duration <= 0
      || duration * 1000 > 8.64e15 - Date.now()) {
      fail(`${base}.${key}`, "must be a positive finite representable duration");
    }
  }
  if (Array.isArray(items)) {
    if (!isJson(items)) fail(`${base}.items`, "static items must contain only JSON values");
    const identities = new Set<string>();
    items.forEach((item, index) => {
      const fields = (ids as string[]).map(key => {
        const value = Object.hasOwn(item, key) ? item[key] : undefined;
        if (typeof value !== "string" || !value.trim()) fail(`${base}.items[${index}].${key}`, "item identity field must be a non-empty string");
        try { return encodeURIComponent(value as string); }
        catch { return fail(`${base}.items[${index}].${key}`, "must be valid Unicode"); }
      });
      const identity = fields.join("/");
      if (identities.has(identity)) fail(`${base}.items[${index}]`, "duplicate composite item identity");
      identities.add(identity);
    });
  }
}

/** Visit fanouts in workflow nodes, loop bodies, templates and goal-loop plans. */
export function validateSubworkflowFanouts(spec: unknown): void {
  const object = (value: unknown): value is Record<string, unknown> =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  function visitWorkflow(workflow: unknown, prefix: string): void {
    if (!object(workflow)) return;
    visit(workflow.nodes, `${prefix}nodes`);
    if (object(workflow.nodeTemplates)) {
      for (const [name, template] of Object.entries(workflow.nodeTemplates)) {
        if (object(template)) visit(template.body, `${prefix}nodeTemplates.${name}.body`);
      }
    }
  }
  function visit(nodes: unknown, prefix: string): void {
    if (!Array.isArray(nodes)) return;
    for (const node of nodes) {
      if (!object(node)) continue;
      const nodePath = `${prefix}[${String(node.id)}]`;
      const executor = object(node.executor) ? node.executor : undefined;
      // ClawMind prefers an executor-form loop; otherwise a legacy node-form
      // loop uses node.body even when an unrelated executor is present.
      if (executor?.type === "loop-group") {
        visit(executor.body, `${nodePath}.executor.body`);
        continue;
      }
      if (node.type === "loop-group") {
        visit(node.body, `${nodePath}.body`);
        continue;
      }
      if (!executor) continue;
      const path = `${nodePath}.executor`;
      if (executor.type === "subworkflow" && Object.hasOwn(executor, "fanout")) {
        if (!record(executor)) throw new Error(`Validation error: ${path} must be a plain object`);
        validateFanout(executor, path);
      }
      if (executor.type === "goal-loop" && object(executor.initialPlan)
        && executor.initialPlan.type === "spec") {
        visitWorkflow(executor.initialPlan.spec, `${path}.initialPlan.spec.`);
      }
    }
  }
  visitWorkflow(spec, "");
}
