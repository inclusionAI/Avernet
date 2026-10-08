/** Utility functions extracted from clawweb langfuse routes for tclog. */

export function col<T>(row: Record<string, unknown>, key: string): T | undefined {
  if (key in row) return row[key] as T;
  const lower = key.toLowerCase();
  for (const k of Object.keys(row)) {
    if (k.toLowerCase() === lower) return row[k] as T;
  }
  return undefined;
}

export function safeParseJson(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
