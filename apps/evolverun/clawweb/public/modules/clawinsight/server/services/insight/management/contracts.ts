export const managementActions = ["close", "reopen", "start", "defer", "reject", "edit", "assign_owner"] as const;
export type ManagementAction = typeof managementActions[number];
export type ManagementActor = { kind: "admin" | "agent"; id: string };
export type ManagementCommand = {
  requestId: string;
  version: number;
  action: ManagementAction;
  reason: string;
  title?: string;
  note?: string;
  suggestedAction?: string;
};
export type ManagementEvent = {
  requestId: string;
  fingerprint: string;
  actor: ManagementActor;
  action: ManagementAction;
  reason: string;
  beforeStatus: string;
  afterStatus: string;
  beforeVersion: number;
  afterVersion: number;
  at: string;
  note?: string;
  suggestedAction?: string;
};
export class ManagementError extends Error {
  constructor(public readonly code: string, message: string, public readonly status: number) { super(message); }
}
// JSON is one line: user-provided newlines cannot inject legacy governance events.
export function managementEvents(text: string | null): ManagementEvent[] {
  const events: ManagementEvent[] = [];
  for (const match of (text ?? "").matchAll(/^\[改进项管理\]\n记录：(.*)$/gm)) {
    try {
      const value = JSON.parse(match[1]);
      if (value && managementActions.includes(value.action) && typeof value.requestId === "string"
        && typeof value.fingerprint === "string" && typeof value.actor?.id === "string"
        && Number.isInteger(value.afterVersion)) events.push(value);
    } catch { /* Unrelated legacy prose is not a management receipt. */ }
  }
  return events;
}
export function serializeManagementEvent(event: ManagementEvent): string {
  return JSON.stringify(event).replace(/\[/g, "\\u005b").replace(/\]/g, "\\u005d");
}
export function appendManagementEvent(text: string | null, event: ManagementEvent): string {
  return [text, `[改进项管理]\n记录：${serializeManagementEvent(event)}`].filter(Boolean).join("\n\n");
}
