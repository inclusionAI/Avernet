/**
 * Detect failure indicators in a JSON result object.
 *
 * Checks multiple levels:
 * 1. Top-level: `{success: false}` or `{status: "FAILED"}`
 * 2. Nested MCP pattern: `{success: true, data: {data: "<json>"}}` where the
 *    inner JSON string contains `{success: false}` or `{status: "FAILED"}`
 *
 * Returns an error message string if a failure is detected, or null
 * if the result looks successful.
 */
export function jsonFailureError(result: unknown): string | null {
  if (typeof result !== "object" || result === null) return null;
  const r = result as Record<string, unknown>;

  // Check top-level {success: false}
  if (r.success === false) {
    const msg = r.error ?? r.message ?? r.errorMessage ?? "JSON result indicates failure (success: false)";
    return typeof msg === "string" && msg.trim() ? msg : "JSON result indicates failure (success: false)";
  }

  // Check top-level {status: "FAILED"}
  if (r.status === "FAILED") {
    const msg = r.errorMessage ?? r.displayMarkdown ?? r.message ?? "JSON result indicates failure (status: FAILED)";
    return typeof msg === "string" && msg.trim() ? msg : "JSON result indicates failure (status: FAILED)";
  }

  // Check nested MCP pattern: {success: true, data: {data: "<json string>"}}
  // The inner JSON string may itself contain {success: false} or {status: "FAILED"}
  if (r.success === true && r.data && typeof r.data === "object" && r.data !== null) {
    const d = r.data as Record<string, unknown>;
    if (typeof d.data === "string") {
      try {
        const inner = JSON.parse(d.data);
        const innerError = jsonFailureError(inner);
        if (innerError) return innerError;
      } catch {
        // Not valid JSON — skip
      }
    }
  }

  return null;
}