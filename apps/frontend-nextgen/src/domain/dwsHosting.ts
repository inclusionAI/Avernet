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
export interface HostingConversations {
  total: number;
  items: HostingConversation[];
}

export function canManageHosting(isOwner: boolean, serviceMode: string, engine: string, deployment: string) {
  return isOwner && serviceMode === 'non-service' && engine === 'openclaw' && deployment !== 'local';
}
