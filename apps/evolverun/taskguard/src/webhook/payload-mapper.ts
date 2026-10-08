/**
 * Payload mapping engine for webhook triggers.
 *
 * Maps webhook request body/headers to workflow params using template expressions:
 * - `$.body.<path>` — dot-notation nested access into JSON body
 * - `$.headers.<name>` — case-insensitive header lookup
 * - `|| 'default'` — fallback value when path is missing or null
 *
 * All values are converted to strings for workflow params.
 */

/**
 * Extract a value from an object using dot-notation path segments.
 * Supports nested objects and array indexes.
 */
export function getByPath(obj: unknown, pathSegments: string[]): unknown {
  let current: unknown = obj;

  for (const segment of pathSegments) {
    if (current === null || current === undefined) {
      return undefined;
    }

    // Array index access: segments like "0", "1", etc.
    if (/^\d+$/.test(segment) && Array.isArray(current)) {
      current = current[parseInt(segment, 10)];
    } else if (typeof current === "object" && !Array.isArray(current)) {
      current = (current as Record<string, unknown>)[segment];
    } else {
      return undefined;
    }
  }

  return current;
}

/**
 * Resolve a single mapping expression to a string value.
 *
 * @param expression - The mapping expression (e.g. `$.body.user.name || 'anonymous'`)
 * @param body - The parsed JSON request body
 * @param headers - The request headers (lowercase keys)
 * @returns The resolved string value
 */
export function resolveMappingValue(
  expression: string,
  body: Record<string, unknown>,
  headers: Record<string, string>,
): string {
  const [pathExpr, defaultExpr] = expression.split("||").map((s) => s.trim());

  const value = extractByPath(pathExpr, body, headers);

  if (value !== undefined && value !== null) {
    return String(value);
  }

  if (defaultExpr) {
    // Strip surrounding quotes (single or double)
    return defaultExpr.replace(/^['"]|['"]$/g, "");
  }

  return "";
}

/**
 * Extract a value from body or headers using a path expression.
 */
function extractByPath(
  pathExpr: string,
  body: Record<string, unknown>,
  headers: Record<string, string>,
): unknown {
  if (pathExpr.startsWith("$.body.")) {
    const path = pathExpr.slice("$.body.".length).split(".");
    return getByPath(body, path);
  }

  if (pathExpr.startsWith("$.headers.")) {
    const headerName = pathExpr.slice("$.headers.".length).toLowerCase();
    // Case-insensitive header lookup
    const match = Object.entries(headers).find(([k]) => k.toLowerCase() === headerName);
    return match ? match[1] : undefined;
  }

  return undefined;
}

/**
 * Apply payload mapping to produce workflow params.
 *
 * @param mapping - Key-value pairs where values are mapping expressions
 * @param body - The parsed JSON request body
 * @param headers - The request headers
 * @returns Record<string, string> of resolved param values
 */
export function mapPayload(
  mapping: Record<string, string>,
  body: Record<string, unknown>,
  headers: Record<string, string>,
): Record<string, string> {
  const params: Record<string, string> = {};

  for (const [key, expression] of Object.entries(mapping)) {
    params[key] = resolveMappingValue(expression, body, headers);
  }

  return params;
}