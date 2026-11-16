import type { MonitoringTarget } from './contracts.js';

export type BotDiagnosisRecoveryConfig = MonitoringTarget & {
  diagnosisEnabled: boolean;
  recoveryEnabled: boolean;
  configJson: string;
  version: number;
};

export interface BotDiagnosisRecoveryConfigStore {
  findForOwner(target: { botId: string; ownerId: string; env: string }): Promise<BotDiagnosisRecoveryConfig | null>;
  find(target: MonitoringTarget): Promise<BotDiagnosisRecoveryConfig | null>;
  findMany(targets: readonly MonitoringTarget[]): Promise<Array<BotDiagnosisRecoveryConfig | null>>;
  setSwitches(target: MonitoringTarget, input: {
    diagnosisEnabled: boolean; recoveryEnabled: boolean; expectedVersion: number; updatedBy: string;
  }): Promise<BotDiagnosisRecoveryConfig>;
}

export type BotNotificationConfig = {
  owner: boolean;
  group: { routeId: string } | null;
};

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Invalid or absent notification settings never inherit an enabled default. */
export function parseBotNotifications(raw: string): BotNotificationConfig {
  const disabled = { owner: false, group: null };
  try {
    const value: unknown = JSON.parse(raw);
    if (!object(value)) return disabled;
    const n = value.notifications;
    if (n === undefined) return disabled;
    if (!object(n) || Object.keys(n).some(key => !['owner', 'group'].includes(key))) return disabled;
    for (const [channel, config] of Object.entries(n)) {
      if (!object(config) || Object.keys(config).some(key => !(channel === 'group' ? ['enabled', 'route_id'] : ['enabled']).includes(key))
        || config.enabled !== undefined && typeof config.enabled !== 'boolean'
        || config.route_id !== undefined && (typeof config.route_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(config.route_id))) return disabled;
      if (channel === 'group' && config.enabled === true && typeof config.route_id !== 'string') return disabled;
    }
    const owner = object(n.owner) && n.owner.enabled === true;
    const group = object(n.group) && n.group.enabled === true && typeof n.group.route_id === 'string'
      ? { routeId: n.group.route_id } : null;
    return { owner, group };
  } catch { return disabled; }
}
