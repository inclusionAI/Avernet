/**
 * DB Config Loader — asynchronously loads application config sections from
 * cm_app_config table (SQLite/MySQL) or clawweb internal API.
 *
 * Each row in cm_app_config stores one top-level YAML config section:
 *   config_key  — the top-level key name (e.g. "execution", "teclaw", "git")
 *   config_yaml — the section content WITHOUT the top-level key and without indent
 *
 * Example: config_key="execution", config_yaml="asyncRun: true\nrunTimeoutMs: 600000"
 *   → parseYaml → { asyncRun: true, runTimeoutMs: 600000 }
 *   → wrap with config_key → { execution: { asyncRun: true, runTimeoutMs: 600000 } }
 *
 * The returned object is merged with the local application.yaml via deepMerge(),
 * where DB values override local values for matching keys. Merging is in-memory only —
 * the local application.yaml file is never modified.
 */

import { parse as parseYaml } from "yaml";
import type { IDatabase, Row } from "../db/types.js";
import type { ApiClient } from "../db/api-client.js";

type ConfigRow = {
  config_key: string;
  config_yaml: string;
  version: number;
};

/**
 * Load all enabled config sections from DB or clawweb API.
 * Returns a merged object where each config_key is wrapped as a top-level key.
 */
export async function loadAppConfigFromDB(
  db: IDatabase,
  apiClient?: ApiClient,
): Promise<Record<string, unknown>> {
  let rows: ConfigRow[];

  if (db.dbType === "noop" && apiClient) {
    // API mode: fetch from clawweb internal API
    rows = await loadFromApi(apiClient);
  } else if (db.dbType !== "noop") {
    // SQLite/MySQL mode: query cm_app_config table directly
    rows = await loadFromDb(db);
  } else {
    // No DB and no API client — nothing to load
    return {};
  }

  return mergeConfigRows(rows);
}

async function loadFromDb(db: IDatabase): Promise<ConfigRow[]> {
  try {
    const rows = await db.query<Row & ConfigRow>(
      "SELECT config_key, config_yaml, version FROM cm_app_config WHERE enabled = 1",
    );
    return rows;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[config] Failed to load cm_app_config from DB: ${msg}`);
    return [];
  }
}

async function loadFromApi(apiClient: ApiClient): Promise<ConfigRow[]> {
  try {
    const res = await apiClient.get<{ config_key: string; config_yaml: string; version: number }[]>(
      "/app-config",
    );
    if (!res.ok || !res.data) {
      console.warn(`[config] Failed to load app-config from API: HTTP ${res.status} ${res.error ?? ""}`);
      return [];
    }
    // API returns { success: true, data: [...] } — extract data array
    const data = res.data as unknown as { success?: boolean; data?: ConfigRow[] } | ConfigRow[];
    if (Array.isArray(data)) return data;
    if (data && data.success && Array.isArray(data.data)) return data.data;
    return [];
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`[config] Failed to load app-config from API: ${msg}`);
    return [];
  }
}

/**
 * Merge config rows into a single object.
 * Each row's config_yaml is parsed, then wrapped with config_key as the top-level key.
 *
 * Defensive: if the user accidentally included the top-level key in config_yaml
 * (e.g. wrote "execution:\n  asyncRun: true" instead of "asyncRun: true"),
 * and the parsed result has exactly one key matching config_key, auto-unwrap it.
 */
function mergeConfigRows(rows: ConfigRow[]): Record<string, unknown> {
  const merged: Record<string, unknown> = {};

  for (const row of rows) {
    try {
      let parsed = parseYaml(row.config_yaml) as Record<string, unknown> | null;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        console.warn(`[config] cm_app_config "${row.config_key}": config_yaml is not a valid YAML object, skipping`);
        continue;
      }

      // Defensive unwrap: if user mistakenly included the top-level key
      // e.g. config_yaml = "execution:\n  asyncRun: true"
      // → parsed = { execution: { asyncRun: true } }
      // → unwrap to { asyncRun: true }, then re-wrap below
      const keys = Object.keys(parsed);
      if (keys.length === 1 && keys[0] === row.config_key) {
        parsed = parsed[row.config_key] as Record<string, unknown>;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          console.warn(`[config] cm_app_config "${row.config_key}": unwrapped value is not an object, skipping`);
          continue;
        }
      }

      merged[row.config_key] = parsed;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`[config] cm_app_config "${row.config_key}": failed to parse config_yaml: ${msg}`);
    }
  }

  return merged;
}
