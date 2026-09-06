import { existsSync, readFileSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { parse as parseYaml } from "yaml";

export type ClawWebConfig = Record<string, unknown>;

const BLOCKED_KEYS = new Set(["__proto__", "prototype", "constructor"]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function mergeClawWebConfig(...layers: ClawWebConfig[]): ClawWebConfig {
  const result: ClawWebConfig = {};
  for (const layer of layers) {
    for (const [key, value] of Object.entries(layer)) {
      // COSEC: Ignore prototype mutation keys from configuration documents.
      if (BLOCKED_KEYS.has(key)) continue;
      const current = result[key];
      result[key] = isPlainObject(current) && isPlainObject(value)
        ? mergeClawWebConfig(current, value)
        : value;
    }
  }
  return result;
}

export function loadClawWebConfigFiles(paths: readonly string[]): ClawWebConfig {
  const layers = paths.filter(Boolean).map((filePath) => {
    const resolvedPath = resolve(filePath);
    if (!existsSync(resolvedPath)) throw new Error(`ClawWeb config not found: ${resolvedPath}`);
    const canonicalPath = realpathSync(resolvedPath);
    const parsed = parseYaml(readFileSync(canonicalPath, "utf8"));
    if (parsed == null) return {};
    if (!isPlainObject(parsed)) throw new Error(`ClawWeb config must be a mapping: ${canonicalPath}`);
    return parsed;
  });
  return mergeClawWebConfig(...layers);
}
