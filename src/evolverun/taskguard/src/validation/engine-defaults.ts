/**
 * Engine-injected default values and a "subtractive normalizer" that strips them.
 *
 * Background: the workflow spec is read through two different pipelines with
 * different normalization depth:
 *   - `GET /api/workflows/:id` (the DB read path) runs the engine normalize pass,
 *     which back-fills every node with default `triggerRule`, `retry`, etc.
 *   - `POST /api/workflows/save` (the write path) stores only the fields the user
 *     actually wrote.
 *
 * Comparing a DB-read spec (defaults injected) against a raw local YAML (no
 * defaults) therefore produces false-positive diffs that can never be resolved
 * — "remote has retry / local missing retry, engine auto-injected".
 *
 * `stripEngineDefaults` removes engine-default-valued fields from a spec so both
 * sides converge to "only fields the user explicitly wrote with non-default
 * values", yielding an apples-to-apples comparison. It must mirror the default
 * values produced by `normalizeNode` / `normalizeHookRetry` in `workflow.ts` —
 * if those defaults change, update the constants here too.
 *
 * @module validation/engine-defaults
 */

/** Default node retry (mirrors `normalizeNodeRetry` with `raw == null`). */
export const DEFAULT_NODE_RETRY = {
  maxAttempts: 1,
  backoffMs: 0,
  on: ["executor-failed"],
} as const;

/** Default hook-action retry (mirrors `normalizeHookRetry` with `raw == null`). */
export const DEFAULT_HOOK_RETRY = {
  maxAttempts: 1,
  backoffMs: 0,
} as const;

/** Default trigger rule when `join !== "any"` (mirrors `normalizeTriggerRule`). */
export const DEFAULT_TRIGGER_RULE_ALL = "all_success";
/** Default trigger rule when `join === "any"` (mirrors `normalizeTriggerRule`). */
export const DEFAULT_TRIGGER_RULE_ANY = "one_success";

/**
 * Canonical, key-order-independent JSON string used to compare object/array
 * values for deep equality.
 */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableJson(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function equalsDefault(value: unknown, defaultValue: unknown): boolean {
  try {
    return stableJson(value) === stableJson(defaultValue);
  } catch {
    return false;
  }
}

/**
 * Decide whether a node-level `retry` value equals the engine default for the
 * given node. A node retry is default when it matches {@link DEFAULT_NODE_RETRY}.
 */
function isDefaultNodeRetry(value: unknown): boolean {
  return equalsDefault(value, DEFAULT_NODE_RETRY);
}

/**
 * Strip a default-valued `triggerRule`, taking the node's `join` into account.
 * Returns `undefined` when the value is a default (and should be deleted),
 * otherwise returns the value unchanged.
 */
function stripDefaultTriggerRule(triggerRule: unknown, join: unknown): unknown {
  if (typeof triggerRule !== "string") return triggerRule;
  const defaultRule = join === "any" ? DEFAULT_TRIGGER_RULE_ANY : DEFAULT_TRIGGER_RULE_ALL;
  return triggerRule === defaultRule ? undefined : triggerRule;
}

/**
 * Recursively strip engine-injected default-valued fields from a spec.
 *
 * Operates by key name rather than by structural node detection: `triggerRule`
 * and `retry` have unique semantics inside a workflow spec, so any object that
 * carries them is normalized accordingly. This keeps the traversal simple and
 * robust to shape changes.
 *
 * Returns a new object — does not mutate the input.
 */
export function stripEngineDefaults<T>(spec: T): T {
  if (Array.isArray(spec)) {
    return spec.map(stripEngineDefaults) as T;
  }
  if (!isPlainRecord(spec)) {
    return spec;
  }

  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(spec)) {
    if (key === "triggerRule") {
      const stripped = stripDefaultTriggerRule(val, spec.join);
      if (stripped !== undefined) result[key] = stripped;
      continue;
    }
    if (key === "retry") {
      // Node retry includes `on`; hook retry does not. Match either default.
      if (isDefaultNodeRetry(val) || equalsDefault(val, DEFAULT_HOOK_RETRY)) {
        continue;
      }
      result[key] = stripEngineDefaults(val);
      continue;
    }
    result[key] = stripEngineDefaults(val);
  }
  return result as T;
}