import type { MonitoringTarget } from './contracts.js';

export type BotDiagnosisRecoveryConfig = MonitoringTarget & {
  diagnosisEnabled: boolean;
  recoveryEnabled: boolean;
  configJson: string;
  version: number;
};

export type BotConfigurationWrite = {
  diagnosisEnabled: boolean;
  recoveryEnabled: boolean;
  expectedVersion: number;
  updatedBy: string;
};

export interface BotDiagnosisRecoveryConfigStore {
  resolveTargetForOwner(target: { botId: string; ownerId: string; env: string }): Promise<MonitoringTarget | null>;
  findForOwner(target: { botId: string; ownerId: string; env: string }): Promise<BotDiagnosisRecoveryConfig | null>;
  find(target: MonitoringTarget): Promise<BotDiagnosisRecoveryConfig | null>;
  findMany(targets: readonly MonitoringTarget[]): Promise<Array<BotDiagnosisRecoveryConfig | null>>;
  setSwitches(target: MonitoringTarget, input: BotConfigurationWrite): Promise<BotDiagnosisRecoveryConfig>;
  replaceConfiguration(target: MonitoringTarget, input: BotConfigurationWrite & { configJson: string }): Promise<BotDiagnosisRecoveryConfig>;
}

export type BotNotificationConfig = {
  owner: boolean;
  group: { routeId: string } | null;
};

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

const disabledNotifications: BotNotificationConfig = { owner: false, group: null };

function readBotNotifications(value: unknown): BotNotificationConfig | null {
  if (!object(value)) return null;
  const n = value.notifications;
  if (n === undefined) return { ...disabledNotifications };
  if (!object(n) || Object.keys(n).some(key => !['owner', 'group'].includes(key))) return null;
  for (const [channel, config] of Object.entries(n)) {
    if (!object(config) || Object.keys(config).some(key => !(channel === 'group' ? ['enabled', 'route_id'] : ['enabled']).includes(key))
      || config.enabled !== undefined && typeof config.enabled !== 'boolean'
      || config.route_id !== undefined && (typeof config.route_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(config.route_id))) return null;
    if (channel === 'group' && config.enabled === true && typeof config.route_id !== 'string') return null;
  }
  const owner = object(n.owner) && n.owner.enabled === true;
  const group = object(n.group) && n.group.enabled === true && typeof n.group.route_id === 'string'
    ? { routeId: n.group.route_id } : null;
  return { owner, group };
}

function decodeBotNotifications(raw: string): BotNotificationConfig | null {
  try { return readBotNotifications(JSON.parse(raw)); }
  catch { return null; }
}

export function validBotConfiguration(raw: string): boolean {
  return decodeBotNotifications(raw) !== null;
}

/** Invalid or absent notification settings never inherit an enabled default. */
export function parseBotNotifications(raw: string): BotNotificationConfig {
  return decodeBotNotifications(raw) ?? { ...disabledNotifications };
}
