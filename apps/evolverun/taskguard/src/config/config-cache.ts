/**
 * In-memory config cache — bridges async DB config loading with sync loadConfig().
 *
 * Lifecycle:
 *   1. Engine starts → loadConfig() returns local application.yaml config (cache not ready)
 *   2. initConfig() loads DB config, merges with local, writes to cache
 *   3. Subsequent loadConfig() calls return cached merged config (sync, zero overhead)
 *
 * The cache stores the merged result of local application.yaml + DB cm_app_config.
 * Neither source file is ever modified — merging happens purely in memory.
 */

import type { DatabaseConfig } from "../db/types.js";
import type { AppConfig } from "./types.js";

type CachedConfig = { database: DatabaseConfig; app: AppConfig };

let _cache: CachedConfig | null = null;

export function getCachedConfig(): CachedConfig | null {
  return _cache;
}

export function setCachedConfig(config: CachedConfig): void {
  _cache = config;
}

export function isConfigReady(): boolean {
  return _cache !== null;
}

export function clearCachedConfig(): void {
  _cache = null;
}
