const PREFIX = 'tc_';

export const STORAGE_KEYS = {
  WORKSPACE_ACTIVE_SESSION: 'workspace_active_session',
  BOT_WORKSHOP_FILTER: 'bot_workshop_filter',
  MARKET_FILTER: 'market_filter',
  SCHEDULED_TASK_FILTER: 'scheduled_task_filter',
  LAYOUT_NAV_COLLAPSED: 'layout_nav_collapsed',
} as const;

export type StorageKeyName = keyof typeof STORAGE_KEYS;

export function storageKey(moduleName: string, key: string): string {
  return `${PREFIX}${moduleName}_${key}`;
}

export function teamclawStorageKey(name: StorageKeyName): string {
  return `${PREFIX}${STORAGE_KEYS[name]}`;
}
