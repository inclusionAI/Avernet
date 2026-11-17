import { watchStateDescription } from '../aistudio-train-state.js';
import type Database from 'better-sqlite3';
import { migrations } from '@avernet/clawweb-shared/server/schema';

/** Deliberately PRE-AIS: exercise the unmodified v71 config schema only. */
export function createAIStudioStateFixture(raw: Database.Database): void {
  for (const sql of migrations.find(m => m.version === 71)!.sql) raw.exec(sql);
}
export function patchWatchFixture(raw: Database.Database, patch: Record<string, unknown>): void {
  const rows = raw.prepare("SELECT config_key,config_yaml FROM cm_app_config WHERE config_key LIKE 'aisw:%'").all() as { config_key:string;config_yaml:string }[];
  for (const row of rows) {
    const value={...JSON.parse(row.config_yaml),...patch};
    raw.prepare('UPDATE cm_app_config SET config_yaml=?,description=? WHERE config_key=?')
      .run(JSON.stringify(value),watchStateDescription(value),row.config_key);
  }
}
