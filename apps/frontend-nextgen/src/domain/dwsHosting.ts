export interface HostingSettings {
  rules: string;
  window: 'always' | 'night' | 'custom';
  custom_window: string;
  timezone: string;
  persona: string;
}
export interface HostingConfig {
  version: number;
  settings: HostingSettings;
}
export interface HostingStatus {
  enabled: boolean;
  running: boolean;
  intake_ready: boolean;
  error: string;
  authorization: string;
  challenge: { verification_url: string; user_code: string } | null;
  unknown_executions: number;
  unknown_deliveries: number;
}
export interface HostingConversation {
  conversation_id: string;
  taken_over: boolean;
}

export function isHostingStatus(value: unknown): value is HostingStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const status = value as Record<string, unknown>;
  return (
    ['enabled', 'running', 'intake_ready'].every((key) => typeof status[key] === 'boolean') &&
    typeof status.error === 'string' &&
    typeof status.authorization === 'string' &&
    ['unknown_executions', 'unknown_deliveries'].every(
      (key) => Number.isInteger(status[key]) && (status[key] as number) >= 0,
    ) &&
    (status.challenge === null ||
      (typeof status.challenge === 'object' &&
        status.challenge !== null &&
        typeof (status.challenge as Record<string, unknown>).verification_url === 'string' &&
        typeof (status.challenge as Record<string, unknown>).user_code === 'string'))
  );
}
export interface HostingConversations {
  total: number;
  items: HostingConversation[];
}

export function canManageHosting(isOwner: boolean, serviceMode: string, engine: string, deployment: string) {
  return isOwner && serviceMode === 'non-service' && engine === 'openclaw' && deployment !== 'local';
}
