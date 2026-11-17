import type { AgentControlPlaneEnvironment } from '../common/task-control-plane.js';

export type RecoveryAppConfigStore = { findByKey(key: string): Promise<{ enabled: number; config_json: string } | null> };
export type RecoveryNotifications = { owner: boolean; group: { webhook: string; timeout_seconds: number } | null };
export type RecoveryCardConfiguration = { template_id: string; callback_route_key?: string };

export function cardConfiguration(value: unknown): Partial<Record<AgentControlPlaneEnvironment, RecoveryCardConfiguration>> {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid recovery card configuration");
  for (const [environment, item] of Object.entries(value)) {
    if (!["pre", "prod"].includes(environment) || !item || typeof item !== "object" || Array.isArray(item)
      || Object.keys(item).some(key => !["template_id", "callback_route_key"].includes(key))
      || typeof item.template_id !== "string" || !item.template_id.trim() || item.template_id.length > 256
      || item.callback_route_key !== undefined && (typeof item.callback_route_key !== "string" || !item.callback_route_key.trim() || item.callback_route_key.length > 256)) throw new Error("Invalid recovery card configuration");
  }
  return value;
}
