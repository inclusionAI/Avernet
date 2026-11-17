import type { migrations } from './schema.js';
import { aistudioTrainManualAuditMigrations } from './schema-aistudio-train-manual-audit.js';

/** Retired migration ID: AIStudio state now uses disabled existing cm_app_config
 * rows. Keep the ledger slot for upgrades from already-applied installations;
 * intentionally no DDL, DROP, data conversion or schema ledger rewriting. */
export const aistudioTrainMigrations: typeof migrations = [{
  version: 155,
  description: 'Retired AIStudio training watch table migration (no DDL)',
  sql: [],
}, ...aistudioTrainManualAuditMigrations];
