import type { migrations } from './schema.js';

/** Retired, not a placeholder: manual-audit markers now live in existing disabled
 * cm_app_config rows. Preserve v156 for ledger compatibility; never create/drop
 * a table, rewrite historical events or change already-applied ledger entries. */
export const aistudioTrainManualAuditMigrations: typeof migrations = [{
  version: 156,
  description: 'Retired AIStudio manual audit table migration (no DDL)',
  sql: [],
}];
