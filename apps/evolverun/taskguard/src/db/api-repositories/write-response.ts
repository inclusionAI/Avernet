import type { ApiResponse } from "../api-client/types.js";

/** Retain HTTP status so callers do not retry permanent 404/409 rejections. */
export function assertWriteAccepted(response: ApiResponse<unknown>, operation: string): void {
  const data = response.data;
  const rejected = data === false || (
    data !== null && typeof data === "object" && "success" in data && data.success === false
  );
  if (!response.ok || response.status < 200 || response.status >= 300 || rejected) {
    throw Object.assign(
      new Error(`${operation} rejected by server: HTTP ${response.status} ${response.error ?? ""}`.trim()),
      { status: response.status },
    );
  }
}
