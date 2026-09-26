/**
 * safeJsonStringify — JSON.stringify that never throws.
 *
 * Executor results (MCP/tool/embedded-agent raw responses) can contain values
 * plain JSON.stringify rejects: circular references, BigInt, throwing toJSON,
 * proxies. When such a value reaches a persistence boundary (TaskFlow
 * stateJson, flow_runs.result_json, node_executions output_json) a throwing
 * stringify silently kills the whole DB write — the flow then appears
 * "running" forever and gets reaped by the timeout watchdog even though every
 * node succeeded. This helper trades a small amount of fidelity (the
 * unserializable subtree is replaced by a marker) for never losing the write.
 */

const CIRCULAR_MARKER = "[Circular]";
const UNSERIALIZABLE_MARKER = "[Unserializable]";

function bigintToSafe(value: bigint): number | string {
  const asNumber = Number(value);
  return Number.isSafeInteger(asNumber) ? asNumber : value.toString();
}

/**
 * Like JSON.stringify, but:
 * - circular references serialize as "[Circular]"
 * - BigInt serializes as number when safe, decimal string otherwise
 * - any other serialization failure retries with per-value sanitization,
 *   then degrades to `fallback` instead of throwing.
 *
 * Note: data loss is possible at the poisoned subtree (by design). Callers
 * that need to know sanitization happened can pass `onSanitized`.
 */
export function safeJsonStringify(
  value: unknown,
  fallback = "{}",
  onSanitized?: (reason: string) => void,
): string {
  // ancestors tracks only the CURRENT traversal path (via the replacer's
  // `this`, which is the parent of each value). A WeakSet of all visited
  // objects would false-positive on shared (non-circular) references that
  // appear in two places of the state — those must serialize twice fully.
  const ancestors: unknown[] = [];
  try {
    const out = JSON.stringify(value, function (_key, v) {
      if (typeof v === "bigint") {
        onSanitized?.("bigint");
        return bigintToSafe(v);
      }
      if (typeof v === "object" && v !== null) {
        // eslint-disable-next-line @typescript-eslint/no-this-alias -- replacer `this` is the traversal parent
        const parent = this as unknown;
        while (ancestors.length > 0 && ancestors[ancestors.length - 1] !== parent) ancestors.pop();
        if (ancestors.includes(v)) {
          onSanitized?.("circular");
          return CIRCULAR_MARKER;
        }
        ancestors.push(v);
      }
      return v;
    });
    return out ?? fallback;
  } catch (firstError) {
    // Rare path: a throwing toJSON/getter/proxy defeated the replacer.
    // Sanitize per-value so one poisonous subtree doesn't kill everything.
    onSanitized?.(firstError instanceof Error ? firstError.message : String(firstError));
    try {
      const ancestorsDeep: unknown[] = [];
      const out = JSON.stringify(value, function (_key, v) {
        if (typeof v === "bigint") return bigintToSafe(v);
        if (typeof v === "function" || typeof v === "symbol") return UNSERIALIZABLE_MARKER;
        if (typeof v === "object" && v !== null) {
          const parent = this as unknown;
          while (ancestorsDeep.length > 0 && ancestorsDeep[ancestorsDeep.length - 1] !== parent) ancestorsDeep.pop();
          if (ancestorsDeep.includes(v)) return CIRCULAR_MARKER;
          ancestorsDeep.push(v);
          try {
            JSON.stringify(v);
          } catch {
            return UNSERIALIZABLE_MARKER;
          }
        }
        return v;
      });
      return out ?? fallback;
    } catch {
      return fallback;
    }
  }
}