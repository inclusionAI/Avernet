import type { ServerConfig, ServerConfigMap } from './servers.config';

type LogLevel = 'silent' | 'error' | 'warn' | 'debug';
export interface PresetConfig {
  name: string;
  servers: ServerConfig;
  logLevel: LogLevel;
}

// eslint-disable-next-line @typescript-eslint/no-var-requires
const SERVERS = require('./servers.config').SERVERS as ServerConfigMap;

export const PRESETS = {
  local: { name: 'Local', servers: SERVERS.LOCAL, logLevel: 'debug' as const },
  dev: { name: 'Development', servers: SERVERS.DEV, logLevel: 'warn' as const },
  pre: { name: 'Pre-production', servers: SERVERS.PRE, logLevel: 'warn' as const },
  prod: { name: 'Production', servers: SERVERS.PROD, logLevel: 'silent' as const },
} as const;

export type PresetName = keyof typeof PRESETS;
