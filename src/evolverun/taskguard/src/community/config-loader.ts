/**
 * Community default config loader.
 * Loads configs/application.yaml with SQLite defaults.
 */
import * as path from "node:path";
import * as fs from "node:fs";
import yaml from "js-yaml";

export function loadCommunityConfig(configPath?: string): Record<string, unknown> {
  const defaultPath = path.resolve(
    import.meta.dirname ?? ".",
    "../../configs/application.yaml"
  );
  const resolved = configPath ?? defaultPath;

  if (!fs.existsSync(resolved)) {
    return { database: { type: "sqlite", sqlite: { path: "~/.taskguard/workflow/engine.db" } } };
  }

  return yaml.load(fs.readFileSync(resolved, "utf-8")) as Record<string, unknown>;
}
