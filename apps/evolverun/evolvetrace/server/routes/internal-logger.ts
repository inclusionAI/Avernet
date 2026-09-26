/** Simplified internal API logger for Evolvetrace. */
export function apiLog(_op: string, _path: string, _data: Record<string, unknown>): void {
  // No-op in open-source version; could log to console if needed.
}

export function apiLogBody(_op: string, _path: string, _body: unknown, _meta?: Record<string, unknown>): void {
  // No-op.
}
