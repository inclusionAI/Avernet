/** Format HTTP file logs in process-local time for the CDO text collector. */
export function httpLogLevel(status: unknown): "INFO" | "ERROR" {
  return typeof status === "number" && status >= 400 ? "ERROR" : "INFO";
}

export function formatHttpLog(now: Date, level: "INFO" | "ERROR", scope: string, entry: object): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  const timestamp = local.toISOString().slice(0, -1).replace("T", " ").replace(".", ",");
  return `${timestamp} ${level} [${scope}] ${JSON.stringify(entry)}`;
}
